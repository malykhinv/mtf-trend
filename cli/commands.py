"""CLI command handlers for data, anomaly research, and cache maintenance."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from logging import Logger
from pathlib import Path
from typing import Callable, cast

import pandas as pd

from config import AppConfig
from constants import (
    DEFAULT_QUALITY_REPORT_OUTPUT_FILE,
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
from data.liquidity.daily_volume_ranker import DailyVolumeRanker
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
from vectorbt_runner import DataPreparer

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
        results_dir=config.backtest.results_dir,
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
            _parse_grid_values,
            _parse_grid_exit_rules,
            _parse_grid_profile_values,
            collect_pair_anomaly_rows_for_configs,
            run_anomaly_strategy_backtest,
        )
        from research_tools.anomaly_config import ANOMALY_BACKTEST_TIMEFRAME_PAIRS

        output_dir = (
            Path(str(args.output_dir))
            if getattr(args, "output_dir", None)
            else config.backtest.results_dir / "anomaly_lab"
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
            feature_contract = (
                "htf_setup_ltf_entry_v1" if entry_timeframe != setup_timeframe else "closed_setup_tf_v1"
            )
            return AnomalyBacktestConfig(
                lab_config=lab_config,
                setup_timeframe=setup_timeframe,
                entry_timeframe=entry_timeframe,
                feature_contract=feature_contract,
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
                max_market_entry_drift_pct=float(getattr(args, "max_market_entry_drift_pct", 0.003)),
                min_market_rr_to_signal_tp1=float(getattr(args, "min_market_rr_to_signal_tp1", 0.75)),
                tp1_r=float(args.tp1_r),
                tp1_fraction=float(args.tp1_fraction),
                trail_lookback_candles=int(args.trail_lookback_candles),
                trail_buffer_r=float(args.trail_buffer_r),
                exit_rule=str(getattr(args, "exit_rule", "structural_trail")),
                max_hold_candles=int(args.max_hold_candles),
                fee_rate=float(args.fee_rate),
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
                    precollected_by_pair[(setup_timeframe, entry_timeframe)] = pd.read_csv(candidate_path)
                    loaded_pairs.append((setup_timeframe, entry_timeframe))
                    print(
                        f"anomaly-lab: reused candidates {setup_timeframe}/{entry_timeframe} <- {candidate_path}",
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
                print(
                    "anomaly-lab: collecting candidates in one symbol-major pass across timeframe pairs",
                    flush=True,
                )
                precollected_by_pair = collect_pair_anomaly_rows_for_configs(
                    collection_configs,
                    symbols=getattr(args, "symbols", None),
                    progress_label="anomaly candidates tf-set",
                    include_derivatives_context=False,
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
        from research_tools.anomaly_micro_live import _aggregate_aggtrades_to_ohlcv_frame, _resolve_aggtrade_id, _resolve_aggtrade_timestamp

        logger = get_logger("backfill-anomaly-aggtrade-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
        _, exchange_client, _ = _build_fetch_stack(config)
        symbols = _resolve_fetch_symbol_list(config=config, args=args, logger=logger)
        max_symbols = getattr(args, "max_symbols", None)
        if max_symbols is not None:
            symbols = symbols[: int(max_symbols)]
        start_timestamp_ms, end_timestamp_ms = _fetch_period(config, int(args.days), getattr(args, "end_timestamp_ms", None))
        chunk_ms = int(args.chunk_hours) * 3_600_000
        storage = ParquetStorage(
            base_dir=config.backtest.cache_dir,
            log_level=config.backtest.log_level,
            logs_dir=config.backtest.logs_dir,
        )
        rows: list[dict[str, object]] = []
        for symbol_index, symbol in enumerate(symbols, start=1):
            symbol_added = 0
            symbol_skipped_chunks = 0
            symbol_fetched_chunks = 0
            symbol_status = "ok"
            symbol_error = ""
            chunk_start = int(start_timestamp_ms)
            logger.warning("aggTrades 1s backfill %s/%s %s", symbol_index, len(symbols), symbol)
            try:
                market_id = exchange_client.get_market_id(symbol)
                while chunk_start <= int(end_timestamp_ms):
                    chunk_end = min(int(end_timestamp_ms), chunk_start + chunk_ms - 1)
                    existing_first = storage.get_first_timestamp(symbol, Timeframe.S1)
                    existing_last = storage.get_last_timestamp(symbol, Timeframe.S1)
                    if existing_first is not None and existing_last is not None and existing_first <= chunk_start and existing_last >= chunk_end:
                        symbol_skipped_chunks += 1
                        chunk_start = chunk_end + 1
                        continue
                    symbol_fetched_chunks += 1
                    all_rows: list[dict[str, object]] = []
                    next_from_id: int | None = None
                    previous_last_id: int | None = None
                    while True:
                        params: dict[str, object] = {
                            "symbol": market_id,
                            "limit": 1000,
                            "startTime": int(chunk_start),
                            "endTime": int(chunk_end),
                        }
                        if next_from_id is not None:
                            params = {
                                "symbol": market_id,
                                "limit": 1000,
                                "fromId": int(next_from_id),
                                "endTime": int(chunk_end),
                            }
                        batch = exchange_client.fetch_binance_agg_trades(symbol=symbol, params=params)
                        if not batch:
                            break
                        all_rows.extend(dict(row) for row in batch)
                        last_row = batch[-1]
                        last_id = _resolve_aggtrade_id(dict(last_row))
                        last_ts = _resolve_aggtrade_timestamp(dict(last_row))
                        if last_id is None or (previous_last_id is not None and last_id <= previous_last_id):
                            break
                        previous_last_id = last_id
                        next_from_id = last_id + 1
                        if last_ts is None or last_ts >= chunk_end or len(batch) < 1000:
                            break
                    if all_rows:
                        frame = _aggregate_aggtrades_to_ohlcv_frame(
                            pd.DataFrame(all_rows),
                            timeframe_ms=1000,
                            start_timestamp_ms=int(chunk_start),
                            end_timestamp_ms=int(chunk_end),
                        )
                        if not frame.empty:
                            frame["aggregation_source"] = "binance_futures_aggTrades"
                            frame["aggregation_target_timeframe"] = "1s"
                            frame["aggregation_version"] = "p166_aggtrades_to_1s_v1"
                            symbol_added += storage.save_incremental(symbol, Timeframe.S1, frame)
                    chunk_start = chunk_end + 1
            except Exception as exc:
                symbol_status = "error"
                symbol_error = f"{type(exc).__name__}: {exc}"
                logger.exception("aggTrades 1s backfill failed for %s: %s", symbol, exc)
            rows.append(
                {
                    "symbol": symbol,
                    "status": symbol_status,
                    "error": symbol_error,
                    "added_rows": int(symbol_added),
                    "fetched_chunks": int(symbol_fetched_chunks),
                    "skipped_existing_chunks": int(symbol_skipped_chunks),
                    "start_timestamp_ms": int(start_timestamp_ms),
                    "end_timestamp_ms": int(end_timestamp_ms),
                    "start_timestamp_utc": datetime.fromtimestamp(int(start_timestamp_ms) / 1000, UTC).isoformat(),
                    "end_timestamp_utc": datetime.fromtimestamp(int(end_timestamp_ms) / 1000, UTC).isoformat(),
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


def run_anomaly_live(config: AppConfig, args: argparse.Namespace) -> int:
    """Запускает строгий REST-only micro-live цикл anomaly wake-up."""

    def _run() -> int:
        from research_tools.anomaly_micro_live import (
            AnomalyMicroLiveRunner,
            LiveAnomalyConfig,
            LiveStartupError,
            build_telegram_config_from_env,
        )

        live_config = LiveAnomalyConfig(
            results_dir=config.backtest.results_dir,
            symbols=tuple(getattr(args, "symbols", None) or ()),
            confirm_real_orders=bool(getattr(args, "confirm_real_orders", False)),
            cache_dir=config.backtest.cache_dir,
            pump_categories=tuple(
                item.strip()
                for item in str(
                    getattr(
                        args,
                        "pump_categories",
                        "runner_oi_confirmed,runner_flow,runner_reclaim,runner_balanced",
                    )
                ).split(",")
                if item.strip()
            ),
            baseline_candles=int(getattr(args, "baseline_candles", 60)),
            confirmation_candles=int(getattr(args, "confirmation_candles", 4)),
            min_quote_ratio_start=float(getattr(args, "min_quote_ratio_start", 4.0)),
            min_trade_ratio_start=float(getattr(args, "min_trade_ratio_start", 4.0)),
            min_price_retention=float(getattr(args, "min_price_retention", 0.65)),
            min_verticality_score=float(getattr(args, "min_verticality_score", 0.20)),
            min_hold_count=int(getattr(args, "min_hold_count", 2)),
            min_oi_change_pct_3x5m=(
                None
                if getattr(args, "min_oi_change_pct_3x5m", None) is None
                else float(args.min_oi_change_pct_3x5m)
            ),
            max_initial_risk_pct=float(getattr(args, "max_initial_risk_pct", 0.16)),
            stop_buffer_range_fraction=float(getattr(args, "stop_buffer_range_fraction", 0.05)),
            max_prior_up_down_whipsaw_to_impulse_range=(
                None
                if getattr(args, "max_prior_up_down_whipsaw_to_impulse_range", None) is None
                else float(args.max_prior_up_down_whipsaw_to_impulse_range)
            ),
            position_notional_usdt=float(getattr(args, "position_notional_usdt", 12.0)),
            max_open_positions=int(getattr(args, "max_open_positions", 3)),
            exclude_default_high_cap_symbols=_to_bool_flag(
                getattr(args, "exclude_default_high_cap_symbols", True),
                default=True,
            ),
            symbol_batch_size=int(getattr(args, "symbol_batch_size", 20)),
            inactive_scan_slots_per_cycle=getattr(args, "inactive_scan_slots_per_cycle", None),
            scan_hot_timeframes_per_symbol=_to_bool_flag(
                getattr(args, "scan_hot_timeframes_per_symbol", True),
                default=True,
            ),
            active_symbol_ttl_ms=int(getattr(args, "active_symbol_ttl_ms", 60_000)),
            ticker_radar_enabled=_to_bool_flag(getattr(args, "ticker_radar_enabled", True), default=True),
            live_ws_ticker_enabled=_to_bool_flag(getattr(args, "live_ws_ticker_enabled", True), default=True),
            live_ws_ticker_stale_ms=int(getattr(args, "live_ws_ticker_stale_ms", 5_000)),
            live_ws_ticker_startup_wait_seconds=float(
                getattr(args, "live_ws_ticker_startup_wait_seconds", 10.0)
            ),
            live_ws_ticker_startup_seed_enabled=_to_bool_flag(
                getattr(args, "live_ws_ticker_startup_seed_enabled", True),
                default=True,
            ),
            live_ws_aggtrade_enabled=_to_bool_flag(getattr(args, "live_ws_aggtrade_enabled", True), default=True),
            live_ws_aggtrade_stale_ms=int(getattr(args, "live_ws_aggtrade_stale_ms", 5_000)),
            live_ws_aggtrade_buffer_minutes=int(getattr(args, "live_ws_aggtrade_buffer_minutes", 20)),
            live_ws_aggtrade_max_backfill_ms=int(getattr(args, "live_ws_aggtrade_max_backfill_ms", 360_000)),
            live_aggtrade_rest_cache_ttl_ms=int(getattr(args, "live_aggtrade_rest_cache_ttl_ms", 1_200_000)),
            live_aggtrade_rest_cache_padding_ms=int(getattr(args, "live_aggtrade_rest_cache_padding_ms", 60_000)),
            ticker_radar_interval_seconds=float(getattr(args, "ticker_radar_interval_seconds", 5.0)),
            ticker_radar_watch_ttl_ms=int(getattr(args, "ticker_radar_watch_ttl_ms", 120_000)),
            ticker_radar_watch_batch_size=int(getattr(args, "ticker_radar_watch_batch_size", 5)),
            ticker_radar_max_promotions_per_cycle=int(getattr(args, "ticker_radar_max_promotions_per_cycle", 20)),
            max_precise_scan_symbols_per_cycle=getattr(args, "max_precise_scan_symbols_per_cycle", None),
            latency_sla_controller_enabled=_to_bool_flag(
                getattr(args, "latency_sla_controller_enabled", True),
                default=True,
            ),
            latency_sla_due_scan_p95_seconds=float(
                getattr(args, "latency_sla_due_scan_p95_seconds", 15.0)
            ),
            latency_sla_min_due_samples=int(getattr(args, "latency_sla_min_due_samples", 1)),
            ticker_radar_min_price_delta_pct=float(getattr(args, "ticker_radar_min_price_delta_pct", 0.003)),
            ticker_radar_min_quote_volume_delta_usdt=float(getattr(args, "ticker_radar_min_quote_volume_delta_usdt", 10_000.0)),
            ticker_radar_min_quote_volume_delta_ratio=float(getattr(args, "ticker_radar_min_quote_volume_delta_ratio", 3.0)),
            warm_watch_enabled=_to_bool_flag(getattr(args, "warm_watch_enabled", True), default=True),
            warm_watch_ttl_ms=int(getattr(args, "warm_watch_ttl_ms", 600_000)),
            warm_watch_min_observations_for_precise=int(
                getattr(args, "warm_watch_min_observations_for_precise", 2)
            ),
            warm_watch_min_price_delta_pct=float(getattr(args, "warm_watch_min_price_delta_pct", -0.001)),
            warm_watch_max_price_delta_pct=float(getattr(args, "warm_watch_max_price_delta_pct", 0.012)),
            warm_watch_aggtrade_target_cap=int(getattr(args, "warm_watch_aggtrade_target_cap", 40)),
            prepump_warm_watch_scoring_enabled=_to_bool_flag(
                getattr(args, "prepump_warm_watch_scoring_enabled", False),
                default=False,
            ),
            prepump_warm_watch_profile_csv=getattr(args, "prepump_warm_watch_profile_csv", None),
            prepump_warm_watch_min_abs_standardized_diff=float(
                getattr(args, "prepump_warm_watch_min_abs_standardized_diff", 0.75)
            ),
            prepump_warm_watch_min_runner_rows=int(
                getattr(args, "prepump_warm_watch_min_runner_rows", 10)
            ),
            prepump_warm_watch_min_fader_rows=int(
                getattr(args, "prepump_warm_watch_min_fader_rows", 10)
            ),
            prepump_warm_watch_max_features=int(getattr(args, "prepump_warm_watch_max_features", 8)),
            prepump_warm_watch_score_weight=float(getattr(args, "prepump_warm_watch_score_weight", 0.35)),
            prepump_warm_watch_windows=str(getattr(args, "prepump_warm_watch_windows", "30m,1h,2h,6h")),
            prepump_warm_watch_min_coverage_ratio=float(
                getattr(args, "prepump_warm_watch_min_coverage_ratio", 0.80)
            ),
            symbol_context_snapshot_enabled=_to_bool_flag(
                getattr(args, "symbol_context_snapshot_enabled", True),
                default=True,
            ),
            symbol_context_snapshot_interval_seconds=float(
                getattr(args, "symbol_context_snapshot_interval_seconds", 60.0)
            ),
            symbol_context_snapshot_symbols_per_cycle=int(
                getattr(args, "symbol_context_snapshot_symbols_per_cycle", 20)
            ),
            symbol_context_snapshot_fresh_ms=int(getattr(args, "symbol_context_snapshot_fresh_ms", 900_000)),
            symbol_context_snapshot_max_cycle_seconds=float(
                getattr(args, "symbol_context_snapshot_max_cycle_seconds", 0.75)
            ),
            max_signal_age_ms=int(getattr(args, "max_signal_age_ms", 60_000)),
            max_entry_price_drift_pct=float(getattr(args, "max_entry_price_drift_pct", 0.003)),
            min_executable_rr_to_signal_tp1=float(getattr(args, "min_executable_rr_to_signal_tp1", 0.75)),
            max_position_amount_slippage_ratio=float(getattr(args, "max_position_amount_slippage_ratio", 0.05)),
            scan_sleep_seconds=float(getattr(args, "scan_sleep_seconds", 2.0)),
            live_ohlcv_cache_enabled=_to_bool_flag(getattr(args, "live_ohlcv_cache_enabled", True), default=True),
            live_ohlcv_cache_write_enabled=_to_bool_flag(
                getattr(args, "live_ohlcv_cache_write_enabled", True),
                default=True,
            ),
            live_ohlcv_cache_flush_interval_seconds=float(
                getattr(args, "live_ohlcv_cache_flush_interval_seconds", 30.0)
            ),
            live_ohlcv_cache_max_buffer_rows=int(getattr(args, "live_ohlcv_cache_max_buffer_rows", 50_000)),
            live_ohlcv_cache_flush_max_symbol_timeframes=getattr(
                args,
                "live_ohlcv_cache_flush_max_symbol_timeframes",
                4,
            ),
            delayed_replay_enabled=_to_bool_flag(getattr(args, "delayed_replay_enabled", False), default=False),
            delayed_replay_delay_seconds=float(getattr(args, "delayed_replay_delay_seconds", 300.0)),
            delayed_replay_min_idle_seconds=float(getattr(args, "delayed_replay_min_idle_seconds", 45.0)),
            delayed_replay_max_cases_per_cycle=int(getattr(args, "delayed_replay_max_cases_per_cycle", 3)),
            delayed_replay_max_cycle_seconds=float(getattr(args, "delayed_replay_max_cycle_seconds", 1.5)),
            delayed_replay_max_queue_size=int(getattr(args, "delayed_replay_max_queue_size", 2000)),
            delayed_replay_outcome_lookahead_seconds=float(
                getattr(args, "delayed_replay_outcome_lookahead_seconds", 300.0)
            ),
            signal_scan_backfill_candles=int(getattr(args, "signal_scan_backfill_candles", 10)),
            max_cycles=getattr(args, "max_cycles", None),
            trail_lookback_candles=int(getattr(args, "trail_lookback_candles", 5)),
            trail_buffer_r=float(getattr(args, "trail_buffer_r", 0.10)),
        )
        _, exchange_client, _ = _build_fetch_stack(config)
        runner = None
        try:
            runner = AnomalyMicroLiveRunner(
                config=live_config,
                telegram=build_telegram_config_from_env(),
                exchange_client=exchange_client,
            )
            return runner.run()
        except KeyboardInterrupt:
            if runner is not None:
                runner.shutdown(reason="command_keyboard_interrupt")
            raise
        except LiveStartupError as exc:
            print(f"live: запуск остановлен: {exc}", flush=True)
            return 2

    return _run_with_logging("run-anomaly-live", config, _run)


def run_anomaly_top_growth(config: AppConfig, args: argparse.Namespace) -> int:
    """Exports standalone closed-hour top-growth artifacts."""

    def _run() -> int:
        from research_tools.anomaly_micro_live import (
            LiveStartupError,
            TopGrowthSnapshotConfig,
            TopGrowthSnapshotRunner,
            parse_top_growth_period_start_ms,
        )

        visibility_events_csv_arg = getattr(args, "visibility_events_csv", None)
        top_growth_config = TopGrowthSnapshotConfig(
            results_dir=config.backtest.results_dir,
            symbols=tuple(getattr(args, "symbols", None) or ()),
            period_start_ms=parse_top_growth_period_start_ms(getattr(args, "period_start_utc", None)),
            min_return_pct=float(getattr(args, "top_growth_min_return_pct", 0.10)),
            limit=int(getattr(args, "top_growth_limit", 5)),
            fetch_spacing_seconds=float(getattr(args, "top_growth_fetch_spacing_seconds", 0.05)),
            visibility_events_csv=Path(visibility_events_csv_arg) if visibility_events_csv_arg else None,
        )
        _, exchange_client, _ = _build_fetch_stack(config)
        try:
            return TopGrowthSnapshotRunner(
                config=top_growth_config,
                exchange_client=exchange_client,
            ).run()
        except LiveStartupError as exc:
            print(f"top-growth: запуск остановлен: {exc}", flush=True)
            return 2

    return _run_with_logging("run-anomaly-top-growth", config, _run)


def check_quality(config: AppConfig, args: argparse.Namespace) -> int:
    """Проверяет качество и целостность данных."""
    return _run_with_logging("check-quality", config, lambda: _check_quality_inner(config, args))


def clear_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Очищает директорию локального кэша и пересоздаёт её."""
    return _run_with_logging("clear-cache", config, lambda: _clear_cache_inner(config, args))
