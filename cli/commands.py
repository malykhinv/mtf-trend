"""CLI command handlers for data, anomaly research, and cache maintenance."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import shutil
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from logging import Logger
from pathlib import Path
from typing import Callable, Iterable, cast

import pandas as pd

from config import AppConfig
from constants import (
    DEFAULT_ANOMALY_BACKTEST_MAX_OPEN_POSITIONS,
    DEFAULT_QUALITY_REPORT_OUTPUT_FILE,
    DEFAULT_EXECUTABLE_ENTRY_PRICE_DRIFT_PCT,
    DEFAULT_SLIPPAGE,
    OI_STALE_MIN_OBSERVATIONS,
    OI_STALE_RATIO_THRESHOLD,
    QUALITY_OI_LEADING_GAPS_ISSUE,
    QUALITY_OI_MISSING_COLUMN_ISSUE,
    QUALITY_OI_MISSING_VALUES_ISSUE,
    QUALITY_OI_STALE_SERIES_ISSUE,
    QUALITY_SEVERITY_CRITICAL,
    QUALITY_SEVERITY_ERROR,
    QUALITY_SEVERITY_INFO,
    QUALITY_SEVERITY_WARNING,
)
from data.clients.noop_market_data_client import NoOpMarketDataClient
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.fetchers.derivatives_context_fetcher import DerivativesContextFetcher
from data.fetchers.market_data_fetcher import MarketDataFetcher
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from data.quality.data_validator import DataValidator
from data.quality.gap_detector import GapDetector
from data.storage.parquet_storage import ParquetStorage
from domain.enums.exchange import Exchange
from domain.enums.timeframe import Timeframe
from domain.models.reporting.quality_report import QualityReport
from domain.models.reporting.quality_summary import QualitySummary
from domain.models.reporting.quality_symbol_stats import QualitySymbolStats
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from utils.logger import get_logger
from utils.symbols import normalize_symbol

def _to_bool_flag(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default



def _normalized_symbol_tuple(symbols: Iterable[str] | None) -> tuple[str, ...]:
    if symbols is None:
        return ()
    normalized = {
        normalize_symbol(str(symbol))
        for symbol in symbols
        if str(symbol).strip()
    }
    return tuple(sorted(symbol for symbol in normalized if symbol))


def _reuse_universe_symbol_scope(symbols: Iterable[str] | None) -> str:
    return "explicit_symbols" if _normalized_symbol_tuple(symbols) else "cache_snapshot_scan"


def _parse_run_config_sequence(raw: object, *, path: Path, column: str) -> tuple[str, ...]:
    if _is_missing_csv_value(raw):
        return ()
    text = str(raw).strip()
    if not text:
        return ()
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        parsed = [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(parsed, str):
        values = [parsed]
    elif isinstance(parsed, (list, tuple, set)):
        values = list(parsed)
    else:
        raise ValueError(f"reused candidates run_config.csv {column} is not a sequence: {path}")
    return tuple(sorted({normalize_symbol(str(value)) for value in values if str(value).strip()}))


def _is_missing_csv_value(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _parse_run_config_mapping(raw: object, *, path: Path) -> dict[str, object]:
    if isinstance(raw, dict):
        return dict(raw)
    if _is_missing_csv_value(raw):
        raise ValueError(f"reused candidates run_config.csv has empty lab_config: {path}")
    text = str(raw).strip()
    if not text:
        raise ValueError(f"reused candidates run_config.csv has empty lab_config: {path}")
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError) as exc:
        sanitized = re.sub(r"(?:WindowsPath|PosixPath)\((['\"])(.*?)\1\)", r"\1\2\1", text)
        try:
            parsed = ast.literal_eval(sanitized)
        except (SyntaxError, ValueError) as sanitized_exc:
            raise ValueError(f"reused candidates run_config.csv has unparsable lab_config: {path}") from sanitized_exc
    if not isinstance(parsed, dict):
        raise ValueError(f"reused candidates run_config.csv lab_config is not a dict: {path}")
    return dict(parsed)


def _read_reused_candidates_run_config(run_config_path: Path) -> dict[str, object]:
    if not run_config_path.exists():
        raise FileNotFoundError(
            "refusing --reuse-candidates-dir without run_config.csv next to anomaly_candidates.csv: "
            f"{run_config_path}"
        )
    frame = pd.read_csv(run_config_path)
    if frame.empty:
        raise ValueError(f"reused candidates run_config.csv is empty: {run_config_path}")
    row = dict(frame.iloc[0].to_dict())
    lab_config = _parse_run_config_mapping(row.get("lab_config"), path=run_config_path)
    for key, value in lab_config.items():
        row[f"lab_config.{key}"] = value
    return row


def _reuse_config_value_equal(expected: object, actual: object) -> bool:
    if _is_missing_csv_value(expected):
        return _is_missing_csv_value(actual)
    if _is_missing_csv_value(actual):
        return False
    if isinstance(expected, bool):
        return _to_bool_flag(actual) == expected
    if isinstance(expected, int) and not isinstance(expected, bool):
        try:
            return int(float(str(actual))) == expected
        except (TypeError, ValueError):
            return False
    if isinstance(expected, float):
        try:
            return abs(float(actual) - expected) <= 1e-12
        except (TypeError, ValueError):
            return False
    return str(actual) == str(expected)


def _validate_reused_candidates_config(
    *,
    source_config: dict[str, object],
    expected_config: object,
    symbols: Iterable[str] | None,
    run_config_path: Path,
) -> None:
    lab_config = expected_config.lab_config
    expected_universe_scope = _reuse_universe_symbol_scope(symbols)
    expected_symbols = _normalized_symbol_tuple(symbols)
    source_universe_scope = str(source_config.get("universe_symbol_scope", "")).strip()
    if not source_universe_scope:
        raise ValueError(
            "refusing --reuse-candidates-dir because run_config.csv does not declare universe_symbol_scope; "
            f"regenerate candidates with the guarded universe contract: {run_config_path}"
        )
    source_symbols = _parse_run_config_sequence(
        source_config.get("universe_requested_symbols_normalized"),
        path=run_config_path,
        column="universe_requested_symbols_normalized",
    )
    expected_values: dict[str, object] = {
        "universe_symbol_scope": expected_universe_scope,
        "feature_contract": expected_config.feature_contract,
        "setup_timeframe": expected_config.setup_timeframe,
        "entry_timeframe": expected_config.entry_timeframe,
        "lab_config.timeframe": lab_config.timeframe,
        "lab_config.days": lab_config.days,
        "lab_config.end_timestamp_ms": lab_config.end_timestamp_ms,
        "lab_config.baseline_candles": lab_config.baseline_candles,
        "lab_config.confirmation_candles": lab_config.confirmation_candles,
        "lab_config.forward_high_candles": lab_config.forward_high_candles,
        "lab_config.forward_low_candles": lab_config.forward_low_candles,
        "lab_config.min_quote_ratio_start": lab_config.min_quote_ratio_start,
        "lab_config.min_trade_ratio_start": lab_config.min_trade_ratio_start,
    }
    mismatches: list[str] = []
    for key, expected in expected_values.items():
        actual = source_config.get(key)
        if not _reuse_config_value_equal(expected, actual):
            mismatches.append(f"{key}: expected={expected!r} source={actual!r}")
    if expected_universe_scope == "explicit_symbols" and source_symbols != expected_symbols:
        mismatches.append(
            "universe_requested_symbols_normalized: "
            f"expected={expected_symbols!r} source={source_symbols!r}"
        )
    if expected_universe_scope == "cache_snapshot_scan" and source_symbols:
        mismatches.append(
            "universe_requested_symbols_normalized: expected=() "
            f"source={source_symbols!r}"
        )
    if mismatches:
        details = "; ".join(mismatches)
        raise ValueError(
            "refusing --reuse-candidates-dir because anomaly_candidates.csv was collected with a different "
            f"critical config ({run_config_path}): {details}"
        )


def _filter_reused_candidates_to_current_request(
    candidates: pd.DataFrame,
    *,
    expected_config: object,
    symbols: Iterable[str] | None,
    candidate_path: Path,
) -> pd.DataFrame:
    required_columns = {
        "symbol",
        "timestamp_ms",
        "setup_available_timestamp_ms",
        "decision_timestamp_ms",
        "decision_available_timestamp_ms",
        "timestamp_semantics",
        "setup_timeframe",
        "entry_timeframe",
        "feature_contract",
    }
    missing_columns = sorted(required_columns - set(candidates.columns))
    if missing_columns:
        raise ValueError(
            "refusing --reuse-candidates-dir because anomaly_candidates.csv misses required audit columns "
            f"{missing_columns}: {candidate_path}"
        )
    filtered = candidates.copy()
    filtered["decision_timestamp_ms"] = pd.to_numeric(filtered["decision_timestamp_ms"], errors="coerce")
    invalid_ts_count = int(filtered["decision_timestamp_ms"].isna().sum())
    if invalid_ts_count:
        raise ValueError(
            "refusing --reuse-candidates-dir because anomaly_candidates.csv contains invalid "
            f"decision_timestamp_ms rows={invalid_ts_count}: {candidate_path}"
        )
    filtered["decision_available_timestamp_ms"] = pd.to_numeric(filtered["decision_available_timestamp_ms"], errors="coerce")
    invalid_available_ts_count = int(filtered["decision_available_timestamp_ms"].isna().sum())
    if invalid_available_ts_count:
        raise ValueError(
            "refusing --reuse-candidates-dir because anomaly_candidates.csv contains invalid "
            f"decision_available_timestamp_ms rows={invalid_available_ts_count}: {candidate_path}"
        )
    expected_setup = str(expected_config.setup_timeframe)
    expected_entry = str(expected_config.entry_timeframe)
    expected_contract = str(expected_config.feature_contract)
    filtered = filtered.loc[
        filtered["setup_timeframe"].astype(str).eq(expected_setup)
        & filtered["entry_timeframe"].astype(str).eq(expected_entry)
        & filtered["feature_contract"].astype(str).eq(expected_contract)
    ].copy()
    end_ms = expected_config.lab_config.end_timestamp_ms
    if end_ms is not None:
        end_ms = int(end_ms)
        start_ms = int((datetime.fromtimestamp(end_ms / 1000, UTC) - pd.Timedelta(days=int(expected_config.lab_config.days))).timestamp() * 1000)
        filtered = filtered.loc[
            (filtered["decision_available_timestamp_ms"] >= start_ms)
            & (filtered["decision_available_timestamp_ms"] <= end_ms)
        ].copy()
    wanted_symbols = set(_normalized_symbol_tuple(symbols))
    if wanted_symbols:
        filtered = filtered.loc[filtered["symbol"].astype(str).map(normalize_symbol).isin(wanted_symbols)].copy()
    filtered["decision_timestamp_ms"] = filtered["decision_timestamp_ms"].astype("int64")
    filtered["decision_available_timestamp_ms"] = filtered["decision_available_timestamp_ms"].astype("int64")
    return filtered


def _load_reused_candidates(
    candidate_path: Path,
    *,
    expected_config: object,
    symbols: Iterable[str] | None,
) -> pd.DataFrame:
    run_config_path = candidate_path.parent / "run_config.csv"
    source_config = _read_reused_candidates_run_config(run_config_path)
    _validate_reused_candidates_config(
        source_config=source_config,
        expected_config=expected_config,
        symbols=symbols,
        run_config_path=run_config_path,
    )
    candidates = pd.read_csv(candidate_path)
    return _filter_reused_candidates_to_current_request(
        candidates,
        expected_config=expected_config,
        symbols=symbols,
        candidate_path=candidate_path,
    )

def _build_futures_symbol_map(symbols: list[str]) -> dict[str, str]:
    return {
        normalize_symbol(symbol): symbol
        for symbol in symbols
    }


def _run_with_logging(command_name: str, config: AppConfig, body: Callable[[], int]) -> int:
    logger = get_logger(
        command_name,
        level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    logger.debug("Команда запущена: %s", command_name)
    try:
        code = body()
        logger.debug("Команда завершена: %s, код %s", command_name, code)
        return code
    except KeyboardInterrupt:
        message = f"Команда остановлена пользователем: {command_name}"
        logger.info(message)
        return 130
    except Exception as exc:
        logger.exception("Ошибка: %s", exc)
        return 1


def _build_fetch_stack(config: AppConfig) -> tuple[MarketDataFetcher, CcxtFuturesClient, DerivativesContextFetcher]:
    exchange_client = CcxtFuturesClient(
        exchange=Exchange.BINANCE,
        api_key=config.fetch.binance_api_key,
        secret=config.fetch.binance_secret_key,
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
    )
    storage = ParquetStorage(
        base_dir=config.backtest.cache_dir,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    ohlcv_fetcher = OhlcvFetcher(
        exchange_client=exchange_client,
        storage=storage,
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    oi_fetcher = OiFetcher(
        exchange_client=exchange_client,
        storage=storage,
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    market_fetcher = MarketDataFetcher(
        ohlcv_fetcher=ohlcv_fetcher,
        oi_fetcher=oi_fetcher,
        market_data_client=NoOpMarketDataClient(),
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    derivatives_context_fetcher = DerivativesContextFetcher(
        exchange_client=exchange_client,
        cache_dir=config.backtest.cache_dir,
        log_level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    return (market_fetcher, exchange_client, derivatives_context_fetcher)


def _resolve_symbols(
        exchange_client: CcxtFuturesClient,
        top_n: int,
        min_volume_usd: float,
        logger: Logger,
        cache_dir: Path,
        liquidity_timeframe: Timeframe,
        futures_symbols_raw: list[str] | None = None,
) -> tuple[list[str], dict[str, dict[str, object]]]:
    futures_symbols_raw = futures_symbols_raw or exchange_client.get_futures_symbols()
    liquidity_quality_by_symbol: dict[str, dict[str, object]] = {}
    futures_symbol_map = _build_futures_symbol_map(futures_symbols_raw)
    exchange_symbols_normalized = sorted(futures_symbol_map)

    from data.liquidity.daily_volume_ranker import DailyVolumeRanker

    ranker = DailyVolumeRanker(cache_dir=cache_dir)
    symbols_raw = [futures_symbol_map[symbol] for symbol in exchange_symbols_normalized]
    avg_daily_volumes = ranker.calculate_avg_daily_volume_usd(
        symbols=symbols_raw,
        timeframe=liquidity_timeframe,
    )
    avg_daily_volumes_normalized = {
        normalize_symbol(raw_symbol): volume
        for raw_symbol, volume in avg_daily_volumes.items()
    }
    symbols_with_volume = [
        symbol
        for symbol in exchange_symbols_normalized
        if symbol in avg_daily_volumes_normalized
    ]

    liquid_symbols = [
        symbol
        for symbol in symbols_with_volume
        if avg_daily_volumes_normalized.get(symbol, 0.0) >= min_volume_usd
    ]
    combined_volume_score_by_symbol = {
        symbol: avg_daily_volumes_normalized[symbol]
        for symbol in liquid_symbols
    }
    liquidity_score_by_symbol: dict[str, float] = {}
    exchange_liquid_symbols: set[str] = set()

    try:
        ranked_metrics = exchange_client.get_futures_symbols_with_liquidity_metrics()
        for item in ranked_metrics:
            symbol_raw = str(item.get('symbol', ''))
            symbol = normalize_symbol(symbol_raw)
            if symbol not in futures_symbol_map:
                continue

            quote_volume = float(item.get('quote_volume', 0.0) or 0.0)
            liquidity_score = float(item.get('liquidity_score', 0.0) or 0.0)
            liquidity_score_by_symbol[symbol] = liquidity_score
            liquidity_quality_by_symbol[symbol_raw] = {
                'liquidity_score': liquidity_score,
                'quote_volume': quote_volume,
                'quote_volume_source': str(item.get('quote_volume_source', 'unknown')),
                'quote_volume_proxy': float(item.get('quote_volume_proxy', 0.0) or 0.0),
                'trade_count_24h': int(item.get('trade_count_24h', 0) or 0),
                'quality_flags': list(cast(list[object], item.get('quality_flags', []))),
                'quality_metadata': dict(cast(dict[str, object], item.get('quality_metadata', {}))),
            }

            if quote_volume >= min_volume_usd:
                exchange_liquid_symbols.add(symbol)
                combined_volume_score_by_symbol[symbol] = max(
                    combined_volume_score_by_symbol.get(symbol, 0.0),
                    quote_volume,
                )
    except Exception as exc:
        logger.warning(
            "Не удалось получить метрики ликвидности с биржи: %s",
            exc,
        )

    combined_symbols = set(liquid_symbols) | exchange_liquid_symbols
    if not combined_symbols:
        logger.warning(
            "Ликвидные символы не найдены; universe_selection_failed, произвольный fallback по первым символам отключён.",
        )
        liquidity_quality_by_symbol["__universe_selection__"] = {
            "liquidity_score": 0.0,
            "quote_volume": 0.0,
            "quote_volume_source": "none",
            "quote_volume_proxy": 0.0,
            "trade_count_24h": 0,
            "quality_flags": ["universe_selection_failed"],
            "quality_metadata": {
                "exchange_symbols_count": len(exchange_symbols_normalized),
                "cache_symbols_with_volume": len(symbols_with_volume),
                "cache_liquid_symbols": len(liquid_symbols),
                "exchange_liquid_symbols": len(exchange_liquid_symbols),
                "min_volume_usd": float(min_volume_usd),
            },
        }
        return [], liquidity_quality_by_symbol

    ranked_top_symbols = sorted(
        combined_symbols,
        key=lambda symbol: (
            combined_volume_score_by_symbol.get(symbol, 0.0),
            liquidity_score_by_symbol.get(symbol, 0.0),
            symbol,
        ),
        reverse=True,
    )[:top_n]

    logger.debug(
        "Отбор ликвидности: биржа %s, кэш %s, порог прошли %s, выбрано %s.",
        len(exchange_symbols_normalized),
        len(symbols_with_volume),
        len(liquid_symbols),
        len(ranked_top_symbols),
    )
    return [futures_symbol_map[symbol] for symbol in ranked_top_symbols], liquidity_quality_by_symbol


def _resolve_explicit_symbols(
    *,
    requested_symbols: list[str] | None,
    futures_symbols_raw: list[str],
    logger: Logger,
) -> list[str] | None:
    if not requested_symbols:
        return None

    futures_symbol_map = _build_futures_symbol_map(futures_symbols_raw)
    resolved: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw_symbol in requested_symbols:
        normalized = normalize_symbol(raw_symbol)
        resolved_symbol = futures_symbol_map.get(normalized)
        if resolved_symbol is None:
            missing.append(str(raw_symbol))
            continue
        if resolved_symbol in seen:
            continue
        seen.add(resolved_symbol)
        resolved.append(resolved_symbol)

    if missing:
        logger.warning("Пропущены неизвестные символы: %s", ",".join(missing))
    logger.debug("Явный список символов: запрошено %s, найдено %s.", len(requested_symbols), len(resolved))
    return resolved


def _resolve_fetch_anchor_timestamp_ms(config: AppConfig, end_timestamp_ms_raw: int | None) -> int:
    if end_timestamp_ms_raw is not None:
        return int(end_timestamp_ms_raw)

    if config.fetch.anchor_timestamp_ms is not None:
        return int(config.fetch.anchor_timestamp_ms)

    return int(time.time() * 1000)


def _fetch_period(config: AppConfig, days: int, end_timestamp_ms_raw: int | None = None) -> tuple[int, int]:
    if days <= 0:
        raise ValueError("--days must be > 0")
    end_timestamp_ms = _resolve_fetch_anchor_timestamp_ms(config, end_timestamp_ms_raw)
    start_timestamp_ms = end_timestamp_ms - (days * 86_400_000)
    return start_timestamp_ms, end_timestamp_ms


@dataclass(frozen=True, slots=True)
class FetchSummary:
    total_symbols: int
    success_symbols: int
    failed_symbols: int

    @property
    def failed_ratio(self) -> float:
        return (self.failed_symbols / self.total_symbols) if self.total_symbols else 0.0


def _log_fetch_summary(
    logger: Logger,
    total_symbols: int,
    failed_symbols_count: int,
    *,
    emit_log: bool = True,
) -> FetchSummary:
    summary = FetchSummary(
        total_symbols=total_symbols,
        success_symbols=total_symbols - failed_symbols_count,
        failed_symbols=failed_symbols_count,
    )
    if emit_log:
        logger.info(
            "Загрузка завершена: %s из %s, ошибок %s.",
            summary.success_symbols,
            summary.total_symbols,
            summary.failed_symbols,
        )
    return summary


def _fetch_exit_code(failed_symbols_count: int, critical_fail_threshold: int = 1) -> int:
    return 1 if failed_symbols_count >= critical_fail_threshold else 0


def _resolve_liquidity_skip_reason(summary: FetchSummary | None, threshold: float) -> str | None:
    if summary is None:
        return "no_ohlcv_cache_data"
    if summary.success_symbols == 0:
        return "no_ohlcv_cache_data"
    if summary.failed_ratio > threshold:
        return "ohlcv_error_ratio_above_threshold"
    return None


def _log_loaded_coins(logger: Logger, count: int, action: str) -> None:
    templates = {
        "loaded": "Загрузка данных завершена\nСимволов: %s",
        "updated": "Обновление кэша завершено\nСимволов: %s",
    }
    template = templates.get(action)
    if template is None:
        raise ValueError(f"Неподдерживаемое действие: {action}")
    logger.info(template, count)


def _attach_liquidity_quality_metadata(
    results: dict[str, SymbolFetchResult],
    liquidity_quality_by_symbol: dict[str, dict[str, object]],
) -> dict[str, SymbolFetchResult]:
    if not liquidity_quality_by_symbol:
        return results

    enriched: dict[str, SymbolFetchResult] = {}
    for symbol, symbol_result in results.items():
        metadata = liquidity_quality_by_symbol.get(symbol, {})
        if not metadata:
            enriched[symbol] = symbol_result
            continue
        enriched[symbol] = SymbolFetchResult(
            success=symbol_result.success,
            message=symbol_result.message,
            added_rows=symbol_result.added_rows,
            quality_metadata=dict(metadata),
        )
    return enriched


def _resolve_timeframe(value: str | None, *, fallback: Timeframe, argument_name: str) -> Timeframe:
    if value is None:
        return fallback

    normalized = value.strip().lower()
    for timeframe in Timeframe:
        if timeframe.value == normalized:
            return timeframe

    supported = ", ".join(tf.value for tf in Timeframe)
    raise ValueError(f"Некорректное значение {argument_name}: {value}. Поддерживаемые значения: {supported}")


def _resolve_timeframe_sequence(
    raw_values: list[str] | tuple[str, ...] | None,
    *,
    fallback: tuple[Timeframe, ...],
    fallback_timeframe: Timeframe,
    argument_name: str,
    supported: set[Timeframe] | None = None,
) -> tuple[Timeframe, ...]:
    if not raw_values:
        return fallback

    resolved: list[Timeframe] = []
    seen: set[Timeframe] = set()
    for raw_value in raw_values:
        timeframe = _resolve_timeframe(
            str(raw_value),
            fallback=fallback_timeframe,
            argument_name=argument_name,
        )
        if supported is not None and timeframe not in supported:
            supported_values = ", ".join(tf.value for tf in fallback)
            raise ValueError(
                f"{argument_name} supports only timeframes "
                f"{{{supported_values}}}, got {timeframe.value}"
            )
        if timeframe in seen:
            continue
        seen.add(timeframe)
        resolved.append(timeframe)
    if not resolved:
        return fallback
    return tuple(resolved)


def _resolve_fetch_timeframes(args: argparse.Namespace, fallback: tuple[Timeframe, ...]) -> tuple[Timeframe, ...]:
    return _resolve_timeframe_sequence(
        getattr(args, "timeframes", None),
        fallback=fallback,
        fallback_timeframe=Timeframe.M5,
        argument_name="--timeframes",
    )


def _fetch_data_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("fetch-data", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    fetch_timeframes = _resolve_fetch_timeframes(args, config.fetch.timeframes)

    if args.top_n is not None and args.top_n <= 0:
        logger.error("--top-n должен быть > 0")
        return 1
    if args.days <= 0:
        logger.error("--days должен быть > 0")
        return 1

    fetcher, exchange_client, derivatives_context_fetcher = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    all_futures_count = len(futures_symbols)
    explicit_symbols = _resolve_explicit_symbols(
        requested_symbols=getattr(args, "symbols", None),
        futures_symbols_raw=futures_symbols,
        logger=logger,
    )
    min_volume_usd = args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd
    top_n = args.top_n if args.top_n is not None else all_futures_count
    if explicit_symbols is None:
        symbols, liquidity_quality_by_symbol = _resolve_symbols(
            exchange_client,
            top_n=top_n,
            min_volume_usd=min_volume_usd,
            logger=logger,
            cache_dir=config.backtest.cache_dir,
            liquidity_timeframe=config.fetch.timeframe,
            futures_symbols_raw=futures_symbols,
        )
    else:
        symbols = explicit_symbols
        liquidity_quality_by_symbol = {}
    logger.warning("Загрузка данных")
    logger.info("Отобрано %s символов", len(symbols))
    logger.debug("Фьючерсов на бирже: %s.", all_futures_count)
    if not symbols:
        liquidity_quality_by_symbol = {}
        logger.warning("Нет символов для загрузки")
        return 0

    start_timestamp_ms, end_timestamp_ms = _fetch_period(config, args.days, getattr(args, "end_timestamp_ms", None))
    include_open_interest = not _to_bool_flag(getattr(args, "skip_open_interest", False))
    include_derivatives_context = bool(getattr(args, "with_derivatives_context", False)) and not _to_bool_flag(
        getattr(args, "skip_derivatives_context", False)
    )
    failed_symbols: set[str] = set()
    fetch_summaries: dict[Timeframe, FetchSummary] = {}

    def _fetch_for_timeframe(
        requested_timeframe: Timeframe,
        symbols_to_fetch: list[str],
        *,
        emit_log: bool = True,
    ) -> None:
        logger.warning("Таймфрейм: %s", requested_timeframe.value)
        result = fetcher.fetch_all(
            symbols=symbols_to_fetch,
            timeframe=requested_timeframe,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
            include_open_interest=include_open_interest,
        )
        enriched_ohlcv = _attach_liquidity_quality_metadata(result.ohlcv, liquidity_quality_by_symbol)
        result.ohlcv.clear()
        result.ohlcv.update(enriched_ohlcv)
        enriched_open_interest = _attach_liquidity_quality_metadata(result.open_interest, liquidity_quality_by_symbol)
        result.open_interest.clear()
        result.open_interest.update(enriched_open_interest)

        fetch_summaries[requested_timeframe] = _log_fetch_summary(
            logger,
            len(symbols_to_fetch),
            result.failed_symbols_count,
            emit_log=emit_log,
        )
        failed_symbols.update(
            symbol
            for symbol in symbols_to_fetch
            if (symbol in result.ohlcv and not result.ohlcv[symbol].success)
            or (symbol in result.open_interest and not result.open_interest[symbol].success)
            or isinstance(result.market_caps.market_caps.get(symbol), str)
        )

    primary_timeframe = fetch_timeframes[0]
    _fetch_for_timeframe(primary_timeframe, symbols, emit_log=True)

    root_stage_status = "ok"
    followup_symbols = symbols
    if explicit_symbols is None:
        liquidity_summary = fetch_summaries.get(primary_timeframe)
        skip_reason = _resolve_liquidity_skip_reason(
            liquidity_summary,
            config.fetch.liquidity_skip_error_ratio_threshold,
        )
        if skip_reason is None:
            followup_symbols, liquidity_quality_by_symbol = _resolve_symbols(
                exchange_client,
                top_n=top_n,
                min_volume_usd=min_volume_usd,
                logger=logger,
                cache_dir=config.backtest.cache_dir,
                liquidity_timeframe=config.fetch.timeframe,
                futures_symbols_raw=futures_symbols,
            )
            logger.debug("Список ликвидных символов пересчитан: %s.", len(followup_symbols))
        else:
            root_stage_status = "ohlcv_cache_failed"
            logger.warning("Отбор ликвидности пропущен для %s", primary_timeframe.value)
            logger.debug("Причина пропуска отбора ликвидности: %s", skip_reason)

    for timeframe in fetch_timeframes:
        if timeframe == primary_timeframe:
            continue
        _fetch_for_timeframe(timeframe, followup_symbols, emit_log=True)

    if include_derivatives_context:
        logger.warning("Derivatives context: 5m/funding")
        derivatives_result = derivatives_context_fetcher.fetch_many(
            followup_symbols,
            start_timestamp_ms,
            end_timestamp_ms,
        )
        derivatives_failed = sum(1 for result in derivatives_result.values() if not result.success)
        _log_fetch_summary(logger, len(followup_symbols), derivatives_failed)
        failed_symbols.update(symbol for symbol, result in derivatives_result.items() if not result.success)
    else:
        logger.debug("Derivatives context не запрошен для bulk cache; anomaly-lab доберёт его лениво для малого набора сигналов.")

    exit_code = _fetch_exit_code(len(failed_symbols))
    if root_stage_status == "ohlcv_cache_failed":
        exit_code = 2

    logger.debug("Загрузка данных завершилась с кодом %s.", exit_code)
    _log_loaded_coins(logger, len(followup_symbols), "loaded")
    return exit_code


def _resolve_fetch_symbol_list(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
) -> list[str]:
    _, exchange_client, _ = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    explicit_symbols = _resolve_explicit_symbols(
        requested_symbols=getattr(args, "symbols", None),
        futures_symbols_raw=futures_symbols,
        logger=logger,
    )
    if explicit_symbols is not None:
        return explicit_symbols

    all_futures_count = len(futures_symbols)
    min_volume_usd = args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd
    top_n = args.top_n if args.top_n is not None else all_futures_count
    symbols, _ = _resolve_symbols(
        exchange_client,
        top_n=top_n,
        min_volume_usd=min_volume_usd,
        logger=logger,
        cache_dir=config.backtest.cache_dir,
        liquidity_timeframe=config.fetch.timeframe,
        futures_symbols_raw=futures_symbols,
    )
    return symbols


def _clone_fetch_args(
    args: argparse.Namespace,
    *,
    symbols: list[str],
    timeframes: list[str],
    skip_open_interest: bool,
) -> argparse.Namespace:
    cloned = argparse.Namespace(**vars(args))
    cloned.symbols = list(symbols)
    cloned.timeframes = list(timeframes)
    cloned.skip_open_interest = skip_open_interest
    return cloned


def _update_cache_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("update-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    fetch_timeframes = _resolve_fetch_timeframes(args, config.fetch.timeframes)

    if args.top_n is not None and args.top_n <= 0:
        logger.error("--top-n должен быть > 0")
        return 1
    if args.days <= 0:
        logger.error("--days должен быть > 0")
        return 1

    fetcher, exchange_client, derivatives_context_fetcher = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    all_futures_count = len(futures_symbols)
    explicit_symbols = _resolve_explicit_symbols(
        requested_symbols=getattr(args, "symbols", None),
        futures_symbols_raw=futures_symbols,
        logger=logger,
    )
    min_volume_usd = args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd
    top_n = args.top_n if args.top_n is not None else all_futures_count
    if explicit_symbols is None:
        symbols, liquidity_quality_by_symbol = _resolve_symbols(
            exchange_client,
            top_n=top_n,
            min_volume_usd=min_volume_usd,
            logger=logger,
            cache_dir=config.backtest.cache_dir,
            liquidity_timeframe=config.fetch.timeframe,
            futures_symbols_raw=futures_symbols,
        )
    else:
        symbols = explicit_symbols
        liquidity_quality_by_symbol = {}
    logger.warning("Обновление кэша")
    logger.info("Отобрано %s символов", len(symbols))
    logger.debug("Фьючерсов на бирже: %s.", all_futures_count)
    if not symbols:
        logger.warning("Нет символов для обновления")
        return 0

    start_timestamp_ms, end_timestamp_ms = _fetch_period(config, args.days, getattr(args, "end_timestamp_ms", None))
    include_open_interest = not _to_bool_flag(getattr(args, "skip_open_interest", False))
    include_derivatives_context = bool(getattr(args, "with_derivatives_context", False)) and not _to_bool_flag(
        getattr(args, "skip_derivatives_context", False)
    )
    failed_symbols: set[str] = set()
    for index, timeframe in enumerate(fetch_timeframes):
        logger.warning("Таймфрейм: %s", timeframe.value)
        result = fetcher.fetch_all(
            symbols=symbols,
            timeframe=timeframe,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
            include_open_interest=include_open_interest,
        )
        enriched_ohlcv = _attach_liquidity_quality_metadata(result.ohlcv, liquidity_quality_by_symbol)
        result.ohlcv.clear()
        result.ohlcv.update(enriched_ohlcv)
        enriched_open_interest = _attach_liquidity_quality_metadata(result.open_interest, liquidity_quality_by_symbol)
        result.open_interest.clear()
        result.open_interest.update(enriched_open_interest)
        _log_fetch_summary(logger, len(symbols), result.failed_symbols_count)
        failed_symbols.update(
            symbol
            for symbol in symbols
            if (symbol in result.ohlcv and not result.ohlcv[symbol].success)
            or (symbol in result.open_interest and not result.open_interest[symbol].success)
            or isinstance(result.market_caps.market_caps.get(symbol), str)
        )
        if index < len(fetch_timeframes) - 1:
            logger.warning("Пауза перед следующим таймфреймом: 75с")
            time.sleep(75)

    if include_derivatives_context:
        logger.warning("Derivatives context: 5m/funding")
        derivatives_result = derivatives_context_fetcher.fetch_many(
            symbols,
            start_timestamp_ms,
            end_timestamp_ms,
        )
        derivatives_failed = sum(1 for result in derivatives_result.values() if not result.success)
        _log_fetch_summary(logger, len(symbols), derivatives_failed)
        failed_symbols.update(symbol for symbol, result in derivatives_result.items() if not result.success)
    else:
        logger.debug("Derivatives context не запрошен для bulk cache; anomaly-lab доберёт его лениво для малого набора сигналов.")

    exit_code = _fetch_exit_code(len(failed_symbols))
    _log_loaded_coins(logger, len(symbols), "updated")
    return exit_code


def _run_hourly_levels_inner(config: AppConfig, args: argparse.Namespace) -> int:
    from research_tools.hourly_levels import build_config_from_namespace, run_hourly_level_scan

    logger = get_logger("run-hourly-levels", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    scan_config = build_config_from_namespace(
        args,
        cache_dir=config.backtest.cache_dir,
        output_dir=config.backtest.results_dir / "top_growth_runs" / datetime.now(UTC).strftime("%Y%m%d_%H%M%S"),
    )
    summary = run_hourly_level_scan(scan_config, progress_callback=logger.info)
    logger.info("1h level scan completed: %s", json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_hourly_levels(config: AppConfig, args: argparse.Namespace) -> int:
    return _run_with_logging(
        "run-hourly-levels",
        config,
        lambda: _run_hourly_levels_inner(config, args),
    )


def _collect_oi_quality_issues(frame: pd.DataFrame) -> list[dict[str, str]]:
    if "open_interest" not in frame.columns:
        return [
            {
                "issue_type": QUALITY_OI_MISSING_COLUMN_ISSUE,
                "severity": QUALITY_SEVERITY_ERROR,
                "description": "Отсутствует колонка open_interest",
            }
        ]

    oi = pd.Series(pd.to_numeric(frame["open_interest"], errors="coerce"), index=frame.index)
    issues: list[dict[str, str]] = []

    if oi.isna().any():
        issues.append(
            {
                "issue_type": QUALITY_OI_MISSING_VALUES_ISSUE,
                "severity": QUALITY_SEVERITY_WARNING,
                "description": "Есть сырые пропуски open_interest",
            }
        )

    if len(oi) > 1:
        first_valid = oi.first_valid_index()
        if first_valid is not None:
            leading_missing = oi.loc[:first_valid].isna().sum()
            if leading_missing > 0:
                issues.append(
                    {
                        "issue_type": QUALITY_OI_LEADING_GAPS_ISSUE,
                        "severity": QUALITY_SEVERITY_WARNING,
                        "description": "Обнаружены пропуски open_interest в начале ряда",
                    }
                )

        valid_mask = oi.notna()
        comparison_mask = valid_mask & valid_mask.shift(1, fill_value=False)
        compared_observations = int(comparison_mask.sum())

        if compared_observations >= OI_STALE_MIN_OBSERVATIONS:
            stale_ratio = (oi.diff().eq(0) & comparison_mask).sum() / compared_observations
        else:
            stale_ratio = 0.0

        if stale_ratio > OI_STALE_RATIO_THRESHOLD:
            issues.append(
                {
                    "issue_type": QUALITY_OI_STALE_SERIES_ISSUE,
                    "severity": QUALITY_SEVERITY_ERROR,
                    "description": "open_interest почти не меняется на сырых данных",
                }
            )

    return issues


def _build_quality_recommendations(summary: QualitySummary, symbols: dict[str, QualitySymbolStats]) -> list[str]:
    recommendations: list[str] = []
    if summary.gaps_total > 0:
        recommendations.append("Дозагрузка диапазона: запустите update-cache для символов с пропусками")

    if any(data.gaps > 0 for data in symbols.values()):
        recommendations.append("Проверка таймфрейма: убедитесь, что timeframe совпадает с кэшем")

    if summary.issues_total > 0:
        recommendations.append("Дедупликация и очистка: переcохраните ряды с удалением дублей и аномалий")

    oi_problem_types = {
        QUALITY_OI_MISSING_COLUMN_ISSUE,
        QUALITY_OI_MISSING_VALUES_ISSUE,
        QUALITY_OI_LEADING_GAPS_ISSUE,
        QUALITY_OI_STALE_SERIES_ISSUE,
    }
    if any(problem in oi_problem_types for problem in summary.by_issue_type):
        recommendations.append("Проверка OI-источника: перезапустите загрузку OI и проверьте сырые пропуски/аномалии")

    if summary.issues_total > 0 or summary.gaps_total > 0:
        recommendations.append("Повторная валидация: после исправлений выполните check-quality повторно")

    return recommendations


def _save_quality_report(report: QualityReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".csv":
        symbols = report.symbols
        with output_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["symbol", "issues", "gaps", "warning", "error", "critical", "info"])
            for symbol, data in symbols.items():
                sev = data.by_severity
                writer.writerow(
                    [
                        symbol,
                        data.issues,
                        data.gaps,
                        sev.get(QUALITY_SEVERITY_WARNING, 0),
                        sev.get(QUALITY_SEVERITY_ERROR, 0),
                        sev.get(QUALITY_SEVERITY_CRITICAL, 0),
                        sev.get(QUALITY_SEVERITY_INFO, 0),
                    ]
                )
    else:
        output_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")


def _check_quality_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("check-quality", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    from vectorbt_runner import DataPreparer

    preparer = DataPreparer(config.backtest.cache_dir)
    symbols = args.symbols or preparer.list_symbols(config.fetch.timeframe)
    if not symbols:
        logger.warning("Нет данных для проверки качества")
        return 0

    validator = DataValidator()
    gap_detector = GapDetector()

    issues_by_type: Counter[str] = Counter()
    issues_by_severity: Counter[str] = Counter()
    symbols_report: dict[str, QualitySymbolStats] = {}

    total_issues = 0
    total_gaps = 0
    for symbol in symbols:
        load_result = preparer.load_symbol_data_result(symbol, config.fetch.timeframe)
        frame = load_result.frame
        if not load_result.ok:
            logger.debug(
                "Проверка качества: %s пропущен, %s (%s).",
                symbol,
                load_result.status,
                load_result.reason,
            )
            continue

        issues = validator.validate(symbol, config.fetch.timeframe, frame)
        gaps = gap_detector.detect_gaps(frame, expected_step_ms=config.fetch.timeframe.to_milliseconds())
        oi_quality_issues = _collect_oi_quality_issues(frame)

        all_issue_types = [issue.issue_type for issue in issues]
        all_severities = [issue.severity.value for issue in issues]
        all_issue_types.extend(item["issue_type"] for item in oi_quality_issues)
        all_severities.extend(item["severity"] for item in oi_quality_issues)

        symbol_issue_counter = Counter(all_issue_types)
        symbol_severity_counter = Counter(all_severities)
        issues_by_type.update(symbol_issue_counter)
        issues_by_severity.update(symbol_severity_counter)

        symbol_total_issues = len(issues) + len(oi_quality_issues)
        total_issues += symbol_total_issues
        total_gaps += len(gaps)

        symbols_report[symbol] = QualitySymbolStats(
            issues=symbol_total_issues,
            gaps=len(gaps),
            by_issue_type=dict(sorted(symbol_issue_counter.items())),
            by_severity=dict(sorted(symbol_severity_counter.items())),
        )

        logger.debug(
            "Проверка качества: %s, проблем %s, пропусков %s, проблем OI %s.",
            symbol,
            symbol_total_issues,
            len(gaps),
            len(oi_quality_issues),
        )

    summary = QualitySummary(
        symbols_checked=len(symbols_report),
        issues_total=total_issues,
        gaps_total=total_gaps,
        by_issue_type=dict(sorted(issues_by_type.items())),
        by_severity=dict(sorted(issues_by_severity.items())),
    )
    report = QualityReport(
        summary=summary,
        symbols=symbols_report,
        recommendations=_build_quality_recommendations(summary, symbols_report),
    )

    output_path = Path(args.output) if args.output else config.backtest.results_dir / DEFAULT_QUALITY_REPORT_OUTPUT_FILE
    _save_quality_report(report, output_path)

    logger.warning("Проверка качества завершена\nПроблем: %s\nПропусков: %s", report.summary.issues_total, report.summary.gaps_total)
    logger.info("Отчёт качества: %s", output_path)
    return 0


def _clear_cache_inner(config: AppConfig, _args: argparse.Namespace) -> int:
    logger = get_logger("clear-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)

    cache_dir = config.backtest.cache_dir
    cache_dir_str = str(cache_dir).strip()
    if not cache_dir_str:
        logger.error("Удаление отменено: путь кэша пустой")
        return 1

    resolved_cache_dir = cache_dir.expanduser().resolve()
    home_dir = Path.home().resolve()
    if resolved_cache_dir == Path(resolved_cache_dir.anchor):
        logger.error("Удаление отменено: путь кэша указывает на корень ФС (%s)", resolved_cache_dir)
        return 1

    if resolved_cache_dir == home_dir:
        logger.error("Удаление отменено: путь кэша указывает на домашнюю директорию (%s)", resolved_cache_dir)
        return 1

    logger.warning("Очистка кэша: %s", resolved_cache_dir)
    shutil.rmtree(resolved_cache_dir, ignore_errors=True)
    resolved_cache_dir.mkdir(parents=True, exist_ok=True)
    logger.warning("Кэш очищен")
    return 0


def fetch_data(config: AppConfig, args: argparse.Namespace) -> int:
    """Запускает сценарий загрузки рыночных данных."""
    return _run_with_logging("fetch-data", config, lambda: _fetch_data_inner(config, args))


def update_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Обновляет локальный кэш данных."""
    return _run_with_logging("update-cache", config, lambda: _update_cache_inner(config, args))


def run_anomaly_lab(config: AppConfig, args: argparse.Namespace) -> int:
    """Runs early anomaly-continuation research backtest artifacts."""

    def _run() -> int:
        from research_tools.anomaly_strategy_backtest import (
            AnomalyBacktestConfig,
            AnomalyLabConfig,
            DEFAULT_LATENCY_EXTRA_MS,
            DEFAULT_LATENCY_GRID_MS,
            _parse_grid_values,
            _parse_grid_exit_rules,
            _parse_grid_profile_values,
            PAIR_COLLECTION_MODE_FORMING,
            PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_CONFIRMATION,
            POST_HTF_CLOSE_LTF_CONFIRMATION_CONTRACT,
            build_targeted_flow_coverage,
            collect_pair_anomaly_rows_for_configs,
            ensure_targeted_subminute_flow_cache_for_configs,
            ready_symbols_from_targeted_flow_coverage,
            split_targeted_flow_backfill_artifacts,
            run_anomaly_strategy_backtest,
        )
        from research_tools.anomaly_config import ANOMALY_BACKTEST_TIMEFRAME_PAIRS

        output_dir = (
            Path(str(args.output_dir))
            if getattr(args, "output_dir", None)
            else config.backtest.results_dir / "anomaly_lab"
        )
        requested_symbols_normalized = _normalized_symbol_tuple(getattr(args, "symbols", None))
        if (
            getattr(args, "end_timestamp_ms", None) is not None
            and not requested_symbols_normalized
            and not _to_bool_flag(getattr(args, "allow_cache_snapshot_universe", False))
        ):
            raise ValueError(
                "refusing historical run-anomaly-lab with --end-timestamp-ms and no explicit --symbols: "
                "the default cache scan is a current local cache snapshot, not an as-of historical listing universe. "
                "Pass --symbols from an explicit research universe, or intentionally add "
                "--allow-cache-snapshot-universe true and treat the result as cache-snapshot biased."
            )
        _, _, derivatives_context_fetcher = _build_fetch_stack(config)

        explicit_timeframe = any(
            getattr(args, name, None)
            for name in ("timeframe", "setup_timeframe", "entry_timeframe")
        )
        if explicit_timeframe:
            setup_timeframe = str(
                getattr(args, "setup_timeframe", None)
                or getattr(args, "timeframe", None)
                or ANOMALY_BACKTEST_TIMEFRAME_PAIRS[0][0].value
            )
            timeframe_pairs = ((setup_timeframe, str(getattr(args, "entry_timeframe", None) or setup_timeframe)),)
        else:
            timeframe_pairs = tuple(
                (setup_timeframe.value, entry_timeframe.value)
                for setup_timeframe, entry_timeframe in ANOMALY_BACKTEST_TIMEFRAME_PAIRS
            )

        run_index_rows: list[dict[str, str]] = []
        reuse_candidates_dir = (
            Path(str(getattr(args, "reuse_candidates_dir")))
            if getattr(args, "reuse_candidates_dir", None)
            else None
        )
        collection_mode = "single_pair" if explicit_timeframe else "symbol_major_precollected"

        def _build_timeframe_pair_config(
            setup_timeframe: str,
            entry_timeframe: str,
            pair_output_dir: Path,
        ) -> AnomalyBacktestConfig:
            lab_config = AnomalyLabConfig(
                cache_dir=config.backtest.cache_dir,
                output_dir=pair_output_dir,
                timeframe=setup_timeframe,
                days=int(args.days),
                end_timestamp_ms=getattr(args, "end_timestamp_ms", None),
                baseline_candles=int(args.baseline_candles),
                confirmation_candles=int(args.confirmation_candles),
                forward_high_candles=int(args.forward_high_candles),
                forward_low_candles=int(args.forward_low_candles),
                min_quote_ratio_start=float(args.min_quote_ratio_start),
                min_trade_ratio_start=float(args.min_trade_ratio_start),
            )
            pair_collection_mode = str(getattr(args, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING))
            if pair_collection_mode == PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_CONFIRMATION and entry_timeframe == setup_timeframe:
                raise ValueError("--pair-collection-mode post_htf_close_ltf_confirmation requires --entry-timeframe below --setup-timeframe")
            feature_contract = (
                "closed_setup_tf_v1"
                if entry_timeframe == setup_timeframe
                else (
                    POST_HTF_CLOSE_LTF_CONFIRMATION_CONTRACT
                    if pair_collection_mode == PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_CONFIRMATION
                    else "htf_setup_ltf_entry_v1"
                )
            )
            return AnomalyBacktestConfig(
                lab_config=lab_config,
                setup_timeframe=setup_timeframe,
                entry_timeframe=entry_timeframe,
                feature_contract=feature_contract,
                pair_collection_mode=pair_collection_mode,
                min_price_retention=float(args.min_price_retention),
                max_price_retention=(
                    None if getattr(args, "max_price_retention", None) is None else float(args.max_price_retention)
                ),
                min_verticality_score=float(args.min_verticality_score),
                min_hold_count=int(args.min_hold_count),
                min_oi_change_pct_3x5m=(
                    None
                    if getattr(args, "min_oi_change_pct_3x5m", None) is None
                    else float(args.min_oi_change_pct_3x5m)
                ),
                require_oi_status_ok=bool(getattr(args, "require_oi_status_ok", False)),
                exhaustion_profile=str(getattr(args, "exhaustion_profile", "none")),
                max_start_quote_ratio=(
                    None if getattr(args, "max_start_quote_ratio", None) is None else float(args.max_start_quote_ratio)
                ),
                max_start_trade_ratio=(
                    None if getattr(args, "max_start_trade_ratio", None) is None else float(args.max_start_trade_ratio)
                ),
                max_start_avg_trade_quote_size_ratio=(
                    None
                    if getattr(args, "max_start_avg_trade_quote_size_ratio", None) is None
                    else float(args.max_start_avg_trade_quote_size_ratio)
                ),
                max_start_quote_ratio_per_abs_return=(
                    None
                    if getattr(args, "max_start_quote_ratio_per_abs_return", None) is None
                    else float(args.max_start_quote_ratio_per_abs_return)
                ),
                max_start_trade_ratio_per_abs_return=(
                    None
                    if getattr(args, "max_start_trade_ratio_per_abs_return", None) is None
                    else float(args.max_start_trade_ratio_per_abs_return)
                ),
                max_start_range_pct_ratio_to_baseline=(
                    None
                    if getattr(args, "max_start_range_pct_ratio_to_baseline", None) is None
                    else float(args.max_start_range_pct_ratio_to_baseline)
                ),
                max_prior_up_down_whipsaw_to_impulse_range=(
                    None
                    if getattr(args, "max_prior_up_down_whipsaw_to_impulse_range", None) is None
                    else float(args.max_prior_up_down_whipsaw_to_impulse_range)
                ),
                min_flow_hold_count=(
                    None if getattr(args, "min_flow_hold_count", None) is None else int(args.min_flow_hold_count)
                ),
                max_prior_spike_count_72h=(
                    None
                    if getattr(args, "max_prior_spike_count_72h", None) is None
                    else int(args.max_prior_spike_count_72h)
                ),
                max_prior_fast_fade_count_72h=(
                    None
                    if getattr(args, "max_prior_fast_fade_count_72h", None) is None
                    else int(args.max_prior_fast_fade_count_72h)
                ),
                min_start_lower_wick_to_range=(
                    None
                    if getattr(args, "min_start_lower_wick_to_range", None) is None
                    else float(args.min_start_lower_wick_to_range)
                ),
                max_start_upper_wick_to_range=(
                    None
                    if getattr(args, "max_start_upper_wick_to_range", None) is None
                    else float(args.max_start_upper_wick_to_range)
                ),
                min_next_taker_buy_quote_share=(
                    None
                    if getattr(args, "min_next_taker_buy_quote_share", None) is None
                    else float(args.min_next_taker_buy_quote_share)
                ),
                red_flag_profile=str(getattr(args, "red_flag_profile", "none")),
                min_mark_close_vs_decision_close_basis=(
                    None
                    if getattr(args, "min_mark_close_vs_decision_close_basis", None) is None
                    else float(args.min_mark_close_vs_decision_close_basis)
                ),
                reject_oi_down_mark_discount=bool(getattr(args, "reject_oi_down_mark_discount", False)),
                reject_stale_derivatives_context=bool(getattr(args, "reject_stale_derivatives_context", False)),
                max_start_taker_buy_quote_share_delta=(
                    None
                    if getattr(args, "max_start_taker_buy_quote_share_delta", None) is None
                    else float(args.max_start_taker_buy_quote_share_delta)
                ),
                max_next_taker_buy_quote_share_delta=(
                    None
                    if getattr(args, "max_next_taker_buy_quote_share_delta", None) is None
                    else float(args.max_next_taker_buy_quote_share_delta)
                ),
                max_initial_risk_pct=float(args.max_initial_risk_pct),
                entry_method=str(getattr(args, "entry_method", "market")),
                pullback_box_fraction=float(getattr(args, "pullback_box_fraction", 0.75)),
                entry_timeout_candles=int(getattr(args, "entry_timeout_candles", 60)),
                market_entry_latency_candles=int(getattr(args, "market_entry_latency_candles", 1)),
                latency_enabled=bool(getattr(args, "latency", False)),
                latency_extra_ms=int(DEFAULT_LATENCY_EXTRA_MS),
                max_market_entry_drift_pct=float(
                    getattr(args, "max_market_entry_drift_pct", DEFAULT_EXECUTABLE_ENTRY_PRICE_DRIFT_PCT)
                ),
                min_market_rr_to_signal_tp1=float(getattr(args, "min_market_rr_to_signal_tp1", 0.70)),
                tp1_r=float(args.tp1_r),
                tp1_fraction=float(args.tp1_fraction),
                trail_lookback_candles=int(args.trail_lookback_candles),
                trail_buffer_r=float(args.trail_buffer_r),
                exit_rule=str(getattr(args, "exit_rule", "structural_trail")),
                max_hold_candles=int(args.max_hold_candles),
                max_open_positions=int(getattr(args, "max_open_positions", DEFAULT_ANOMALY_BACKTEST_MAX_OPEN_POSITIONS)),
                fee_rate=float(args.fee_rate),
                entry_slippage_pct=float(getattr(args, "entry_slippage_pct", DEFAULT_SLIPPAGE)),
                exit_slippage_pct=float(getattr(args, "exit_slippage_pct", DEFAULT_SLIPPAGE)),
            )

        def _run_timeframe_pair(
            setup_timeframe: str,
            entry_timeframe: str,
            pair_output_dir: Path,
            *,
            precollected_candidates: pd.DataFrame | None = None,
            collection_mode: str,
        ) -> None:
            backtest_config = _build_timeframe_pair_config(
                setup_timeframe,
                entry_timeframe,
                pair_output_dir,
            )
            print(
                f"anomaly-lab: running {setup_timeframe}/{entry_timeframe} -> {pair_output_dir}",
                flush=True,
            )
            run_anomaly_strategy_backtest(
                backtest_config,
                symbols=getattr(args, "symbols", None),
                precollected_candidates=precollected_candidates,
                run_entry_grid=bool(getattr(args, "run_entry_grid", False)),
                grid_oi3_values=_parse_grid_values(
                    str(getattr(args, "grid_oi3_values", "0.01,0.02,0.03")),
                    cast=float,
                ),
                grid_hold_values=_parse_grid_values(str(getattr(args, "grid_hold_values", "1,2")), cast=int),
                grid_pullback_fractions=_parse_grid_values(
                    str(getattr(args, "grid_pullback_fractions", "0.65,0.75,0.85")),
                    cast=float,
                ),
                grid_exhaustion_profiles=_parse_grid_profile_values(
                    str(getattr(args, "grid_exhaustion_profiles", "none"))
                ),
                grid_exit_rules=_parse_grid_exit_rules(str(getattr(args, "grid_exit_rules", "structural_trail"))),
                run_latency_grid=bool(getattr(args, "latency", False)),
                latency_grid_ms=DEFAULT_LATENCY_GRID_MS,
                derivatives_context_fetcher=(
                    None if collection_mode == "reused_candidates" else derivatives_context_fetcher
                ),
                render_charts=bool(getattr(args, "render_charts", True)),
            )
            run_index_rows.append(
                {
                    "setup_timeframe": setup_timeframe,
                    "entry_timeframe": entry_timeframe,
                    "feature_contract": backtest_config.feature_contract,
                    "collection_mode": collection_mode,
                    "output_dir": str(pair_output_dir),
                }
            )

        precollected_by_pair: dict[tuple[str, str], pd.DataFrame] = {}
        timeframe_pairs_to_run = timeframe_pairs
        if reuse_candidates_dir is not None:
            loaded_pairs: list[tuple[str, str]] = []
            for setup_timeframe, entry_timeframe in timeframe_pairs:
                pair_dir = f"{setup_timeframe}_{entry_timeframe}".replace("/", "_")
                candidate_path = (
                    reuse_candidates_dir / "anomaly_candidates.csv"
                    if explicit_timeframe
                    else reuse_candidates_dir / pair_dir / "anomaly_candidates.csv"
                )
                if candidate_path.exists():
                    pair_output_dir = (
                        output_dir
                        if explicit_timeframe
                        else output_dir / f"{setup_timeframe}_{entry_timeframe}".replace("/", "_")
                    )
                    expected_reuse_config = _build_timeframe_pair_config(
                        setup_timeframe,
                        entry_timeframe,
                        pair_output_dir,
                    )
                    reusable_candidates = _load_reused_candidates(
                        candidate_path,
                        expected_config=expected_reuse_config,
                        symbols=getattr(args, "symbols", None),
                    )
                    precollected_by_pair[(setup_timeframe, entry_timeframe)] = reusable_candidates
                    loaded_pairs.append((setup_timeframe, entry_timeframe))
                    print(
                        f"anomaly-lab: reused candidates {setup_timeframe}/{entry_timeframe} "
                        f"rows={len(reusable_candidates)} <- {candidate_path}",
                        flush=True,
                    )
                else:
                    print(
                        f"anomaly-lab: missing reused candidates {setup_timeframe}/{entry_timeframe} <- {candidate_path}",
                        flush=True,
                    )
            if not loaded_pairs:
                raise FileNotFoundError(f"no reusable anomaly_candidates.csv files found in {reuse_candidates_dir}")
            timeframe_pairs_to_run = tuple(loaded_pairs)
            collection_mode = "reused_candidates"

        if not explicit_timeframe:
            if reuse_candidates_dir is None:
                collection_configs = [
                    _build_timeframe_pair_config(
                        setup_timeframe,
                        entry_timeframe,
                        output_dir / f"{setup_timeframe}_{entry_timeframe}".replace("/", "_"),
                    )
                    for setup_timeframe, entry_timeframe in timeframe_pairs
                ]
                collection_symbols = getattr(args, "symbols", None)
                if _to_bool_flag(getattr(args, "targeted_flow_backfill", True), default=True):
                    targeted_flow_backfill, targeted_flow_materialize = ensure_targeted_subminute_flow_cache_for_configs(
                        collection_configs,
                        symbols=getattr(args, "symbols", None),
                        progress_label="anomaly targeted flow tf-set",
                    )
                    targeted_flow_plan, targeted_flow_fetch = split_targeted_flow_backfill_artifacts(targeted_flow_backfill)
                    targeted_flow_coverage = build_targeted_flow_coverage(
                        backfill=targeted_flow_backfill,
                        materialize=targeted_flow_materialize,
                        configs=collection_configs,
                    )
                    output_dir.mkdir(parents=True, exist_ok=True)
                    targeted_flow_plan.to_csv(output_dir / "targeted_flow_plan.csv", index=False)
                    targeted_flow_fetch.to_csv(output_dir / "targeted_flow_fetch.csv", index=False)
                    targeted_flow_backfill.to_csv(output_dir / "targeted_flow_backfill.csv", index=False)
                    targeted_flow_materialize.to_csv(output_dir / "targeted_flow_materialize.csv", index=False)
                    targeted_flow_coverage.to_csv(output_dir / "targeted_flow_coverage.csv", index=False)
                    ready_symbols = ready_symbols_from_targeted_flow_coverage(targeted_flow_coverage)
                    planned_windows = 0
                    for column in ("merged_targeted_windows", "targeted_windows", "raw_targeted_windows"):
                        if column in targeted_flow_plan.columns:
                            planned_windows = max(planned_windows, int(pd.to_numeric(targeted_flow_plan[column], errors="coerce").fillna(0).sum()))
                    if planned_windows > 0 and not ready_symbols:
                        precollected_by_pair = {
                            (setup_timeframe, entry_timeframe): pd.DataFrame([
                                {
                                    "symbol": "__all__",
                                    "timeframe": setup_timeframe,
                                    "setup_timeframe": setup_timeframe,
                                    "entry_timeframe": entry_timeframe,
                                    "feature_contract": (
                                        "closed_setup_tf_v1"
                                        if setup_timeframe == entry_timeframe
                                        else (
                                            POST_HTF_CLOSE_LTF_CONFIRMATION_CONTRACT
                                            if str(getattr(args, "pair_collection_mode", PAIR_COLLECTION_MODE_FORMING)) == PAIR_COLLECTION_MODE_POST_HTF_CLOSE_LTF_CONFIRMATION
                                            else "htf_setup_ltf_entry_v1"
                                        )
                                    ),
                                    "status": "error",
                                    "error": "no_trusted_targeted_flow_coverage_after_fetch",
                                    "execution_model": "targeted_flow_required_before_pair_collection",
                                }
                            ])
                            for setup_timeframe, entry_timeframe in timeframe_pairs
                        }
                        collection_mode = "targeted_flow_no_trusted_coverage"
                    elif ready_symbols and not getattr(args, "symbols", None):
                        collection_symbols = ready_symbols
                if not precollected_by_pair:
                    print(
                        "anomaly-lab: collecting candidates in one symbol-major pass across timeframe pairs",
                        flush=True,
                    )
                    precollected_by_pair = collect_pair_anomaly_rows_for_configs(
                        collection_configs,
                        symbols=collection_symbols,
                        progress_label="anomaly candidates tf-set",
                        include_derivatives_context=False,
                        auto_targeted_flow_backfill=False,
                    )

        for setup_timeframe, entry_timeframe in timeframe_pairs_to_run:
            pair_output_dir = (
                output_dir
                if explicit_timeframe
                else output_dir / f"{setup_timeframe}_{entry_timeframe}".replace("/", "_")
            )
            _run_timeframe_pair(
                setup_timeframe,
                entry_timeframe,
                pair_output_dir,
                precollected_candidates=precollected_by_pair.get((setup_timeframe, entry_timeframe)),
                collection_mode=collection_mode,
            )

        if not explicit_timeframe:
            output_dir.mkdir(parents=True, exist_ok=True)
            index_path = output_dir / "anomaly_lab_timeframe_runs.csv"
            with index_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=[
                        "setup_timeframe",
                        "entry_timeframe",
                        "feature_contract",
                        "collection_mode",
                        "output_dir",
                    ],
                )
                writer.writeheader()
                writer.writerows(run_index_rows)
            print(f"anomaly-lab: wrote timeframe run index -> {index_path}", flush=True)
        return 0

    return _run_with_logging("run-anomaly-lab", config, _run)


def materialize_anomaly_subminute_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Materializes 1s-derived subminute anomaly entry caches."""

    def _run() -> int:
        from research_tools.anomaly_strategy_backtest import materialize_subminute_entry_caches

        manifest = materialize_subminute_entry_caches(
            cache_dir=config.backtest.cache_dir,
            target_timeframes=getattr(args, "timeframes", None) or ["5s", "15s", "30s"],
            symbols=getattr(args, "symbols", None),
            overwrite=bool(getattr(args, "overwrite", False)),
            progress_label="materialize anomaly subminute cache",
        )
        output_arg = getattr(args, "output", None)
        output_path = (
            Path(str(output_arg))
            if output_arg
            else config.backtest.results_dir / "anomaly_subminute_cache_materialization.csv"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        manifest.to_csv(output_path, index=False)
        status_counts = manifest["status"].value_counts().to_dict() if "status" in manifest.columns else {}
        print(f"wrote anomaly subminute cache manifest to {output_path}")
        print(f"status counts: {status_counts}")
        return 0

    return _run_with_logging("materialize-anomaly-subminute-cache", config, _run)


def backfill_anomaly_aggtrade_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Backfills true 1s OHLCV cache from Binance futures aggTrades."""

    def _run() -> int:
        from data.storage.parquet_storage import ParquetStorage
        from research_tools.anomaly_aggtrade_cache import aggregate_aggtrades_to_ohlcv_frame, resolve_aggtrade_timestamp
        from research_tools.anomaly_strategy_backtest import AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION

        logger = get_logger("backfill-anomaly-aggtrade-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
        _, exchange_client, _ = _build_fetch_stack(config)
        symbols = _resolve_fetch_symbol_list(config=config, args=args, logger=logger)
        max_symbols = getattr(args, "max_symbols", None)
        if max_symbols is not None:
            symbols = symbols[: int(max_symbols)]
        start_arg = getattr(args, "start_timestamp_ms", None)
        if start_arg is not None and getattr(args, "end_timestamp_ms", None) is None:
            raise ValueError("--start-timestamp-ms requires --end-timestamp-ms")
        start_timestamp_ms, end_timestamp_ms = _fetch_period(config, int(args.days), getattr(args, "end_timestamp_ms", None))
        if start_arg is not None:
            start_timestamp_ms = int(start_arg)
            if start_timestamp_ms > int(end_timestamp_ms):
                raise ValueError("--start-timestamp-ms must be <= --end-timestamp-ms")
        window_timestamps = tuple(int(value) for value in (getattr(args, "window_timestamps_ms", None) or ()))
        if window_timestamps:
            before_ms = int(getattr(args, "window_before_ms", 5_400_000))
            after_ms = int(getattr(args, "window_after_ms", 2_700_000))
            raw_windows = [
                (
                    max(int(start_timestamp_ms), int(timestamp_ms) - before_ms),
                    min(int(end_timestamp_ms), int(timestamp_ms) + after_ms),
                )
                for timestamp_ms in sorted(window_timestamps)
            ]
            windows = _merge_timestamp_windows(raw_windows)
        else:
            windows = [(int(start_timestamp_ms), int(end_timestamp_ms))]
        chunk_ms = int(args.chunk_hours) * 3_600_000
        storage = ParquetStorage(
            base_dir=config.backtest.cache_dir,
            log_level=config.backtest.log_level,
            logs_dir=config.backtest.logs_dir,
        )
        def _existing_1s_cache_has_trusted_version(symbol: str) -> bool:
            existing = storage.load(symbol, Timeframe.S1)
            if existing.empty or "aggregation_version" not in existing.columns:
                return False
            versions = {
                str(value).strip()
                for value in existing["aggregation_version"].dropna().unique().tolist()
                if str(value).strip()
            }
            return bool(versions) and versions <= {AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION}

        rows: list[dict[str, object]] = []
        for symbol_index, symbol in enumerate(symbols, start=1):
            symbol_added = 0
            symbol_skipped_chunks = 0
            symbol_fetched_chunks = 0
            symbol_status = "ok"
            symbol_error = ""
            added_by_window: list[dict[str, object]] = []
            existing_1s_cache_trusted = False
            logger.warning("aggTrades 1s backfill %s/%s %s", symbol_index, len(symbols), symbol)
            try:
                existing_1s_cache_trusted = _existing_1s_cache_has_trusted_version(symbol)
                market_id = exchange_client.get_market_id(symbol)
                for window_start, window_end in windows:
                    chunk_start = int(window_start)
                    window_added = 0
                    while chunk_start <= int(window_end):
                        chunk_end = min(int(window_end), chunk_start + chunk_ms - 1)
                        if not window_timestamps:
                            existing_first = storage.get_first_timestamp(symbol, Timeframe.S1)
                            existing_last = storage.get_last_timestamp(symbol, Timeframe.S1)
                            if (
                                existing_1s_cache_trusted
                                and existing_first is not None
                                and existing_last is not None
                                and existing_first <= chunk_start
                                and existing_last >= chunk_end
                            ):
                                symbol_skipped_chunks += 1
                                chunk_start = chunk_end + 1
                                continue
                        symbol_fetched_chunks += 1
                        all_rows: list[dict[str, object]] = []
                        cursor_ms = int(chunk_start)
                        while True:
                            params: dict[str, object] = {
                                "symbol": market_id,
                                "limit": 1000,
                                "startTime": int(cursor_ms),
                                "endTime": int(chunk_end),
                            }
                            batch = exchange_client.fetch_binance_agg_trades(symbol=symbol, params=params)
                            if not batch:
                                break
                            all_rows.extend(dict(row) for row in batch)
                            last_row = batch[-1]
                            last_ts = resolve_aggtrade_timestamp(dict(last_row))
                            if last_ts is None or last_ts < cursor_ms:
                                break
                            if last_ts >= chunk_end or len(batch) < 1000:
                                break
                            cursor_ms = int(last_ts) + 1
                            time.sleep(0.02)
                        if all_rows:
                            frame = aggregate_aggtrades_to_ohlcv_frame(
                                pd.DataFrame(all_rows),
                                timeframe_ms=1000,
                                start_timestamp_ms=int(chunk_start),
                                end_timestamp_ms=int(chunk_end),
                            )
                            if not frame.empty:
                                frame["aggregation_source"] = "binance_futures_aggTrades"
                                frame["aggregation_target_timeframe"] = "1s"
                                frame["aggregation_version"] = AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION
                                added_rows = storage.save_incremental(symbol, Timeframe.S1, frame)
                                symbol_added += added_rows
                                window_added += added_rows
                        chunk_start = chunk_end + 1
                    added_by_window.append(
                        {
                            "start_timestamp_ms": int(window_start),
                            "end_timestamp_ms": int(window_end),
                            "added_rows": int(window_added),
                        }
                    )
            except Exception as exc:
                symbol_status = "error"
                symbol_error = f"{type(exc).__name__}: {exc}"
                logger.exception("aggTrades 1s backfill failed for %s: %s", symbol, exc)
            rows.append(
                {
                    "symbol": symbol,
                    "status": symbol_status,
                    "error": symbol_error,
                    "existing_1s_cache_trusted": bool(existing_1s_cache_trusted),
                    "trusted_1s_cache_version": AGGTRADE_1S_FULL_BUCKET_CACHE_VERSION,
                    "added_rows": int(symbol_added),
                    "fetched_chunks": int(symbol_fetched_chunks),
                    "skipped_existing_chunks": int(symbol_skipped_chunks),
                    "start_timestamp_ms": int(start_timestamp_ms),
                    "end_timestamp_ms": int(end_timestamp_ms),
                    "start_timestamp_utc": datetime.fromtimestamp(int(start_timestamp_ms) / 1000, UTC).isoformat(),
                    "end_timestamp_utc": datetime.fromtimestamp(int(end_timestamp_ms) / 1000, UTC).isoformat(),
                    "targeted_window_count": len(windows) if window_timestamps else 0,
                    "window_timestamps_ms": ",".join(str(value) for value in window_timestamps),
                    "window_before_ms": int(getattr(args, "window_before_ms", 5_400_000)) if window_timestamps else 0,
                    "window_after_ms": int(getattr(args, "window_after_ms", 2_700_000)) if window_timestamps else 0,
                    "windows_json": json.dumps(added_by_window, ensure_ascii=False),
                }
            )
        manifest = pd.DataFrame(rows)
        output_arg = getattr(args, "output", None)
        output_path = (
            Path(str(output_arg))
            if output_arg
            else config.backtest.results_dir / "anomaly_aggtrade_1s_backfill.csv"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        manifest.to_csv(output_path, index=False)
        print(f"wrote anomaly aggTrade 1s backfill manifest to {output_path}")
        print(f"status counts: {manifest['status'].value_counts().to_dict() if 'status' in manifest.columns else {}}")
        return 0 if manifest.empty or not manifest["status"].eq("error").any() else 1

    return _run_with_logging("backfill-anomaly-aggtrade-cache", config, _run)


def _merge_timestamp_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    valid = sorted((int(start), int(end)) for start, end in windows if int(start) <= int(end))
    if not valid:
        return []
    merged: list[tuple[int, int]] = [valid[0]]
    for start, end in valid[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def run_anomaly_live2(config: AppConfig, args: argparse.Namespace) -> int:
    """Run the generation-0 anomaly live2 runtime skeleton."""

    def _run() -> int:
        from research_tools.anomaly_live2 import (
            AnomalyLive2Config,
            AnomalyLive2Runner,
            build_live2_telegram_config_from_env,
        )

        output_arg = getattr(args, "output_dir", None)
        if output_arg:
            output_dir = Path(output_arg)
        else:
            run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            output_dir = config.backtest.results_dir / "live2_anomaly_runs" / run_id
        symbols_arg = getattr(args, "symbols", None)
        symbols = tuple(str(symbol).strip() for symbol in (symbols_arg or ()) if str(symbol).strip())
        print(f"live2 · подготовка · артефакты {output_dir} · symbols {len(symbols) if symbols else 'auto'}", flush=True)
        _, exchange_client, _ = _build_fetch_stack(config)
        runner = AnomalyLive2Runner(
            AnomalyLive2Config(
                output_dir=output_dir,
                symbols=symbols,
                universe_max_symbols=int(getattr(args, "universe_max_symbols", 600)),
                universe_min_quote_volume_24h=float(getattr(args, "universe_min_quote_volume_24h", 30_000.0)),
                universe_min_trade_count_24h=int(getattr(args, "universe_min_trade_count_24h", 0)),
                universe_min_auto_symbols=int(getattr(args, "universe_min_auto_symbols", 300)),
                decision_loop_interval_seconds=float(getattr(args, "decision_loop_interval_seconds", 0.05)),
                decision_backlog_expire_ms=int(getattr(args, "decision_backlog_expire_ms", 5_000)),
                ws_connection_max_age_seconds=float(getattr(args, "ws_connection_max_age_seconds", 84_600.0)),
                user_data_stream_startup_wait_seconds=float(getattr(args, "user_data_stream_startup_wait_seconds", 10.0)),
                user_data_stream_keepalive_interval_seconds=float(getattr(args, "user_data_stream_keepalive_interval_seconds", 1_800.0)),
                mark_price_stale_ms=int(getattr(args, "mark_price_stale_ms", 5_000)),
                mark_price_startup_wait_seconds=float(getattr(args, "mark_price_startup_wait_seconds", 10.0)),
                oi_stale_ms=int(getattr(args, "oi_stale_ms", 720_000)),
                oi_poll_interval_seconds=float(getattr(args, "oi_poll_interval_seconds", 5.0)),
                oi_symbol_cooldown_seconds=float(getattr(args, "oi_symbol_cooldown_seconds", 60.0)),
                oi_lookback_minutes=int(getattr(args, "oi_lookback_minutes", 20)),
                oi_max_symbols_per_cycle=int(getattr(args, "oi_max_symbols_per_cycle", 20)),
                oi_radar_symbol_ttl_ms=int(getattr(args, "oi_radar_symbol_ttl_ms", 60_000)),
                prior_context_stale_ms=int(getattr(args, "prior_context_stale_ms", 1_200_000)),
                prior_context_poll_interval_seconds=float(getattr(args, "prior_context_poll_interval_seconds", 10.0)),
                prior_context_symbol_cooldown_seconds=float(getattr(args, "prior_context_symbol_cooldown_seconds", 600.0)),
                prior_context_lookback_hours=int(getattr(args, "prior_context_lookback_hours", 24)),
                prior_context_max_symbols_per_cycle=int(getattr(args, "prior_context_max_symbols_per_cycle", 10)),
                prior_context_radar_symbol_ttl_ms=int(getattr(args, "prior_context_radar_symbol_ttl_ms", 60_000)),
                prior_context_spike_return_pct=float(getattr(args, "prior_context_spike_return_pct", 0.03)),
                prior_context_fast_fade_retrace_fraction=float(getattr(args, "prior_context_fast_fade_retrace_fraction", 0.55)),
                top_growth_enabled=bool(getattr(args, "top_growth_enabled", True)),
                top_growth_min_return_pct=float(getattr(args, "top_growth_min_return_pct", 0.10)),
                top_growth_limit=int(getattr(args, "top_growth_limit", 5)),
                top_growth_symbols_per_cycle=int(getattr(args, "top_growth_symbols_per_cycle", 1)),
                top_growth_max_cycle_seconds=float(getattr(args, "top_growth_max_cycle_seconds", 0.75)),
                top_growth_fetch_spacing_seconds=float(getattr(args, "top_growth_fetch_spacing_seconds", 0.02)),
            ),
            exchange_client=exchange_client,
            telegram_config=build_live2_telegram_config_from_env(),
        )
        return runner.run()

    return _run_with_logging("run-anomaly-live2", config, _run)


def run_live_order_smoke(config: AppConfig, args: argparse.Namespace) -> int:
    """Run one minimal real Binance order lifecycle smoke."""

    def _run() -> int:
        from research_tools.live_order_smoke import LiveOrderSmokeConfig, LiveOrderSmokeRunner

        _, exchange_client, _ = _build_fetch_stack(config)
        output_arg = getattr(args, "output_dir", None)
        if output_arg:
            output_dir = Path(output_arg)
        else:
            run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            output_dir = config.backtest.results_dir / "live_order_smoke" / run_id
        smoke_config = LiveOrderSmokeConfig(
            symbol=str(getattr(args, "symbol")),
            notional_usdt=float(getattr(args, "notional_usdt", 12.0)),
            max_notional_usdt=float(getattr(args, "max_notional_usdt", 25.0)),
            stop_distance_pct=float(getattr(args, "stop_distance_pct", 0.05)),
            output_dir=output_dir,
            confirm_real_order_smoke=bool(getattr(args, "confirm_real_order_smoke", False)),
            leave_protected_position_open=bool(getattr(args, "leave_protected_position_open", False)),
            replacement_stop_distance_pct=(
                None
                if getattr(args, "replacement_stop_distance_pct", None) is None
                else float(getattr(args, "replacement_stop_distance_pct"))
            ),
            close_position_before_stop_cancel=bool(getattr(args, "close_position_before_stop_cancel", False)),
            verification_attempts=int(getattr(args, "verification_attempts", 5)),
            verification_sleep_seconds=float(getattr(args, "verification_sleep_seconds", 0.5)),
        )
        print(f"smoke: artifacts {output_dir}", flush=True)
        return LiveOrderSmokeRunner(config=smoke_config, exchange_client=exchange_client).run()

    return _run_with_logging("run-live-order-smoke", config, _run)


def run_anomaly_top_growth(config: AppConfig, args: argparse.Namespace) -> int:
    """Exports standalone closed-hour top-growth artifacts."""

    def _run() -> int:
        from research_tools.anomaly_live2.top_growth import (
            TopGrowthSnapshotConfig,
            TopGrowthSnapshotRunner,
            parse_top_growth_period_start_ms,
        )

        top_growth_config = TopGrowthSnapshotConfig(
            output_dir=config.backtest.results_dir / "top_growth_runs" / datetime.now(UTC).strftime("%Y%m%d_%H%M%S"),
            symbols=tuple(getattr(args, "symbols", None) or ()),
            period_start_ms=parse_top_growth_period_start_ms(getattr(args, "period_start_utc", None)),
            min_return_pct=float(getattr(args, "top_growth_min_return_pct", 0.10)),
            limit=int(getattr(args, "top_growth_limit", 5)),
            fetch_spacing_seconds=float(getattr(args, "top_growth_fetch_spacing_seconds", 0.05)),
        )
        _, exchange_client, _ = _build_fetch_stack(config)
        try:
            return TopGrowthSnapshotRunner(
                config=top_growth_config,
                exchange_client=exchange_client,
            ).run()
        except ValueError as exc:
            print(f"top-growth: invalid request: {exc}", flush=True)
            return 2

    return _run_with_logging("run-anomaly-top-growth", config, _run)


def check_quality(config: AppConfig, args: argparse.Namespace) -> int:
    """Проверяет качество и целостность данных."""
    return _run_with_logging("check-quality", config, lambda: _check_quality_inner(config, args))


def clear_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Очищает директорию локального кэша и пересоздаёт её."""
    return _run_with_logging("clear-cache", config, lambda: _clear_cache_inner(config, args))
