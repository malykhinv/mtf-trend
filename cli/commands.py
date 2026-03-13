"""Модуль проекта."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import time
from collections import Counter
from dataclasses import asdict, dataclass
from logging import Logger
from pathlib import Path
from typing import Callable, cast

import pandas as pd

from config import AppConfig
from constants import (
    DEFAULT_BACKTEST_OUTPUT_FILE,
    DEFAULT_STAGE1_EVENTS_OUTPUT_FILE,
    DEFAULT_STAGE1_PLOTS_DIR_NAME,
    DEFAULT_RESULTS_DIR,
    DEFAULT_QUALITY_REPORT_OUTPUT_FILE,
    DEFAULT_REPORT_OUTPUT_FILE,
    OI_STALE_MIN_OBSERVATIONS,
    OI_STALE_RATIO_THRESHOLD,
    QUALITY_OI_LEADING_GAPS_ISSUE,
    QUALITY_OI_MISSING_VALUES_ISSUE,
    QUALITY_OI_STALE_SERIES_ISSUE,
    QUALITY_OI_MISSING_COLUMN_ISSUE,
    QUALITY_SEVERITY_CRITICAL,
    QUALITY_SEVERITY_ERROR,
    QUALITY_SEVERITY_INFO,
    QUALITY_SEVERITY_WARNING,
    REPORT_PROFITABLE_PF_THRESHOLD,
    REPORT_PROFIT_FACTOR_FILTER,
    REPORT_TRADES_COUNT_FILTER,
    LOG_MSG_TASK_COMPLETED,
)
from data.clients.coingecko_client import CoinGeckoClient
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.fetchers.market_data_fetcher import MarketDataFetcher
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from data.liquidity.bee_bite_stage1_plotter import BeeBiteStage1Plotter
from data.liquidity.bee_bite_stage1_selector import BeeBiteStage1Result, BeeBiteStage1Selector
from data.liquidity.daily_volume_ranker import DailyVolumeRanker
from data.quality.data_validator import DataValidator
from data.quality.gap_detector import GapDetector
from data.storage.parquet_storage import ParquetStorage
from domain.enums.entry_trigger import EntryTrigger
from domain.enums.exchange import Exchange
from domain.enums.timeframe import Timeframe
from domain.models.reporting.backtest_report import BacktestReport
from domain.models.reporting.backtest_summary import BacktestSummary
from domain.models.reporting.optimal_parameter_ranges import OptimalParameterRanges
from domain.models.reporting.profitable_variant import ProfitableVariant
from domain.models.reporting.quality_report import QualityReport
from domain.models.reporting.quality_summary import QualitySummary
from domain.models.reporting.quality_symbol_stats import QualitySymbolStats
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
from domain.models.reporting.trade_results_distribution import TradeResultsDistribution
from strategy.bee_bite import (
    BeeBiteParams,
    BeeBiteStrategy,
    get_bee_bite_runtime,
    parse_bee_bite_grid_mode,
    parse_bee_bite_profile_id,
    parse_bee_bite_reclaim_mode,
    parse_bee_bite_retest_mode,
    validate_bee_bite_runtime,
)
from strategy.factory import build_strategy
from utils.logger import get_logger
from utils.retry import RetryExhaustedError
from utils.symbols import normalize_symbol
from vectorbt_runner import BacktestRunner, DataPreparer, SymbolMtfFrames

# region Приватные

_PROGRESS_LOG_EVERY = 100
_STAGE1_REASON_SAMPLE_LIMIT = 3


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


def _resolve_strategy_id(args: argparse.Namespace) -> str:
    strategy_override = getattr(args, "strategy", None)
    if strategy_override is not None:
        normalized = str(strategy_override).strip().lower()
        if normalized != "bee_bite":
            raise ValueError(f"Неподдерживаемый strategy_id: {normalized}")
        return normalized
    return "bee_bite"


def _resolve_results_dir_for_strategy(base_results_dir: Path, strategy_id: str) -> Path:
    if strategy_id != "bee_bite":
        raise ValueError(f"Неподдерживаемый strategy_id: {strategy_id}")
    return base_results_dir / "strategy" / "bee_bite"


def _format_timestamp_ms(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "n/a"
    return pd.to_datetime(timestamp_ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M")


def _format_stage1_reason_sample(result: BeeBiteStage1Result, frame: pd.DataFrame) -> str:
    rows = int(len(frame))
    if frame.empty or "timestamp" not in frame.columns:
        frame_range = "n/a..n/a"
    else:
        timestamps = pd.to_numeric(frame["timestamp"], errors="coerce").dropna()
        if timestamps.empty:
            frame_range = "n/a..n/a"
        else:
            first_ts = int(timestamps.iloc[0])
            last_ts = int(timestamps.iloc[-1])
            frame_range = f"{_format_timestamp_ms(first_ts)}..{_format_timestamp_ms(last_ts)}"

    pump_pct = f"{float(result.pump_percent or 0.0) * 100:.2f}%"
    retain_pct = f"{float(result.retain_ratio or 0.0) * 100:.2f}%"
    volume_ratio = f"{float(result.post_pump_volume_ratio or 0.0):.2f}x"
    rolling_volume = f"{float(result.rolling_volume_usdt or 0.0):.0f}"
    return (
        f"symbol={result.symbol} rows={rows} range={frame_range} reason={result.reason} "
        f"pump={pump_pct} retain={retain_pct} vol_ratio={volume_ratio} rolling_24h_usdt={rolling_volume}"
    )


def _select_primary_stage1_event(events: list[BeeBiteStage1Result]) -> BeeBiteStage1Result:
    sorted_events = sorted(
        events,
        key=lambda item: (
            int(item.pump_start_timestamp or 0),
            int(item.pump_peak_timestamp or 0),
            int(item.stage1_confirmed_timestamp or 0),
        ),
    )
    first_regime: list[BeeBiteStage1Result] = []
    current_regime_peak_ms = 0

    for event in sorted_events:
        pump_start_ms = int(event.pump_start_timestamp or 0)
        pump_peak_ms = int(event.pump_peak_timestamp or pump_start_ms)
        if not first_regime:
            first_regime.append(event)
            current_regime_peak_ms = pump_peak_ms
            continue

        if pump_start_ms <= current_regime_peak_ms:
            first_regime.append(event)
            current_regime_peak_ms = max(current_regime_peak_ms, pump_peak_ms)
            continue

        break

    return max(
        first_regime,
        key=lambda item: (
            float(item.pump_peak_price or 0.0),
            float(item.pump_percent or 0.0),
            float(item.post_pump_volume_ratio or 0.0),
            -int(item.stage1_confirmed_timestamp or 0),
        ),
    )



def _build_bee_bite_params_from_row(
    row: pd.Series,
    *,
    symbol: str,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
) -> BeeBiteParams:
    def _is_missing_scalar(value: object) -> bool:
        if value is None:
            return True
        if value is pd.NA:
            return True
        if isinstance(value, (pd.Series, pd.DataFrame)):
            return False
        if isinstance(value, float):
            return bool(pd.isna(value))
        return False

    def _normalize_enum_raw(value: object) -> str | None:
        return None if _is_missing_scalar(value) else str(value)

    bite_t_max_in_trade_raw = row.get("bite_t_max_in_trade")
    bite_t_max_in_trade = None if _is_missing_scalar(bite_t_max_in_trade_raw) else int(bite_t_max_in_trade_raw)
    if bite_t_max_in_trade is not None and bite_t_max_in_trade < 1:
        raise ValueError("параметр bite_t_max_in_trade должен быть >= 1 или None")
    bite_reclaim_limit_raw = row.get("bite_reclaim_limit_bars")
    if _is_missing_scalar(bite_reclaim_limit_raw):
        bite_reclaim_limit_raw = row.get("bite_reclaim_limit")
    bite_max_age_range_raw = row.get("bite_max_age_range_hours")
    if _is_missing_scalar(bite_max_age_range_raw):
        bite_max_age_range_raw = row.get("bite_max_age_range")
    bite_cooldown_raw = row.get("bite_cooldown_hours")
    if _is_missing_scalar(bite_cooldown_raw):
        bite_cooldown_raw = row.get("bite_cooldown_bars", 8)
    bite_reclaim_mode_raw = row.get("bite_reclaim_mode")
    bite_retest_mode_raw = row.get("bite_retest_mode")
    bite_profile_id_raw = row.get("bite_profile_id")
    bite_grid_mode_raw = row.get("bite_grid_mode")

    return BeeBiteParams(
        bite_lookback=int(row["bite_lookback"]),
        bite_volume_mult=float(row["bite_volume_mult"]),
        bite_retest_window_hours=int(row["bite_retest_window_hours"]),
        bite_min_rr=float(row["bite_min_rr"]),
        bite_tp2_mult=float(row["bite_tp2_mult"]),
        bite_min_move_atr=float(row["bite_min_move_atr"]),
        bite_max_retest_depth=float(row["bite_max_retest_depth"]),
        bite_confirmation_bars=int(row["bite_confirmation_bars"]),
        bite_entry_trigger=EntryTrigger(str(row["bite_entry_trigger"])),
        bite_min_depth_threshold=float(row["bite_min_depth_threshold"]),
        bite_micro_offset=float(row["bite_micro_offset"]),
        bite_reclaim_limit_bars=int(bite_reclaim_limit_raw),
        bite_max_age_range_hours=int(bite_max_age_range_raw),
        bite_cooldown_hours=int(bite_cooldown_raw),
        bite_reclaim_mode=parse_bee_bite_reclaim_mode(_normalize_enum_raw(bite_reclaim_mode_raw), default="strict"),
        bite_retest_mode=parse_bee_bite_retest_mode(_normalize_enum_raw(bite_retest_mode_raw), default="confirmation"),
        symbol=symbol,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        bite_r_trade=float(row["bite_r_trade"]),
        bite_portfolio_risk_limit=float(row["bite_portfolio_risk_limit"]),
        bite_min_stop_atr_ratio=float(row["bite_min_stop_atr_ratio"]),
        bite_t_max_in_trade=bite_t_max_in_trade,
        bite_profile_id=parse_bee_bite_profile_id(_normalize_enum_raw(bite_profile_id_raw), default="A"),
        bite_grid_mode=parse_bee_bite_grid_mode(_normalize_enum_raw(bite_grid_mode_raw), default="baseline"),
    )



def _plot_bee_bite_diagnostics_for_symbols(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
    strategy: BeeBiteStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    params_row: pd.Series,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    log_prefix: str,
) -> None:
    output_dir = Path(getattr(args, "output_dir", None) or (config.backtest.results_dir / "trade_plots"))
    diagnostics_dir = output_dir / "bee_bite_diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    symbols_with_states = 0
    total_trades_generated = 0

    for symbol, mtf_frames in symbol_frames.items():
        params = _build_bee_bite_params_from_row(
            params_row,
            symbol=symbol,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
        )
        strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)
        diagnostics = strategy.consume_last_generation_diagnostics()
        states_obj = diagnostics.get("states", [])
        states = [str(state) for state in states_obj] if isinstance(states_obj, list) else []
        trades_generated = int(diagnostics.get("trades_generated", 0) or 0)
        total_trades_generated += trades_generated
        if states:
            symbols_with_states += 1

        payload = {
            "symbol": symbol,
            "states": states,
            "states_count": dict(Counter(states)),
            "trades_generated": trades_generated,
            "diagnostics": diagnostics,
        }
        output_path = diagnostics_dir / f"{symbol.replace('/', '_')}_diagnostics.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "%s: сохранена диагностическая визуализация bee_bite symbols=%s trades_generated=%s output_dir=%s",
        log_prefix,
        symbols_with_states,
        total_trades_generated,
        diagnostics_dir,
    )
    if symbols_with_states == 0:
        logger.warning("%s: не найдено диагностических данных bee_bite для визуализации", log_prefix)


def _plot_for_strategy(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
    strategy_id: str,
    strategy: object,
    symbol_frames: dict[str, SymbolMtfFrames],
    params_row: pd.Series,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    log_prefix: str,
) -> bool:
    if strategy_id == "bee_bite":
        if not isinstance(strategy, BeeBiteStrategy):
            logger.error("%s: неподдерживаемый тип визуализации для стратегии bee_bite", log_prefix)
            return False
        _plot_bee_bite_diagnostics_for_symbols(
            config=config,
            args=args,
            logger=logger,
            strategy=strategy,
            symbol_frames=symbol_frames,
            params_row=params_row,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            log_prefix=log_prefix,
        )
        return True

    logger.error("%s: визуализация не поддерживается для стратегии %s", log_prefix, strategy_id)
    return False


def _load_plot_params_row_from_results(
    config: AppConfig,
    args: argparse.Namespace,
    *,
    logger: Logger,
    strategy_id: str,
) -> pd.Series | None:
    explicit_csv_path = getattr(args, "results_input", None) or getattr(args, "input", None)
    if explicit_csv_path is not None:
        csv_path = Path(explicit_csv_path)
    else:
        configured_results_dir = Path(config.backtest.results_dir)
        strategy_results_dir = _resolve_results_dir_for_strategy(configured_results_dir, strategy_id)
        fallback_results_dir = _resolve_results_dir_for_strategy(Path(DEFAULT_RESULTS_DIR), strategy_id)
        candidate_paths = [
            strategy_results_dir / config.backtest.results_file_name,
            strategy_results_dir / "results.csv",
            configured_results_dir / config.backtest.results_file_name,
            fallback_results_dir / "results.csv",
            fallback_results_dir / DEFAULT_BACKTEST_OUTPUT_FILE,
        ]
        csv_path = next((candidate for candidate in candidate_paths if candidate.exists()), candidate_paths[0])

    if not csv_path.exists():
        logger.error("plot-from-results: файл результатов не найден: %s", csv_path)
        return None

    frame = pd.read_csv(csv_path)
    if frame.empty:
        logger.error("plot-from-results: файл результатов пустой: %s", csv_path)
        return None

    required_columns_by_strategy = {
        "bee_bite": [
            "bite_profile_id",
            "bite_grid_mode",
            "bite_lookback",
            "bite_volume_mult",
            "bite_retest_window_hours",
            "bite_min_rr",
            "bite_tp2_mult",
            "bite_min_move_atr",
            "bite_max_retest_depth",
            "bite_confirmation_bars",
            "bite_entry_trigger",
            "bite_min_depth_threshold",
            "bite_micro_offset",
            "bite_reclaim_limit_bars",
            "bite_reclaim_mode",
            "bite_retest_mode",
            "bite_max_age_range_hours",
            "bite_cooldown_hours",
            "bite_r_trade",
            "bite_portfolio_risk_limit",
            "bite_min_stop_atr_ratio",
            "bite_t_max_in_trade",
        ],
    }
    required_columns = required_columns_by_strategy.get(strategy_id)
    if required_columns is None:
        logger.error("plot-from-results: неподдерживаемая стратегия %s", strategy_id)
        return None
    missing_columns = [column for column in required_columns if column not in frame.columns]
    if missing_columns:
        logger.error("plot-from-results: отсутствуют обязательные колонки: %s", ", ".join(missing_columns))
        return None

    selected_id_raw = getattr(args, "id", None)
    selected_row: pd.Series
    if selected_id_raw is not None:
        selected_id = int(selected_id_raw)
        id_columns = ("id", "combination_id", "rank")
        matched_by_column: pd.DataFrame | None = None
        for column in id_columns:
            if column not in frame.columns:
                continue
            numeric_column = pd.Series(pd.to_numeric(frame[column], errors="coerce"), index=frame.index)
            match_mask = numeric_column.eq(selected_id)
            matches: pd.DataFrame = frame.loc[match_mask].copy()
            if not matches.empty:
                matched_by_column = matches
                logger.info(
                    "plot-from-results: найдена комбинация по колонке %s, id=%s, совпадений=%s",
                    column,
                    selected_id,
                    len(matches),
                )
                break

        if matched_by_column is not None:
            selected_row = matched_by_column.iloc[0]
        else:
            row_index = selected_id - 1
            if row_index < 0 or row_index >= len(frame):
                logger.error(
                    "plot-from-results: id=%s не найден (нет колонок id/combination_id/rank и номер строки вне диапазона 1..%s)",
                    selected_id,
                    len(frame),
                )
                return None
            selected_row = frame.iloc[row_index]
            logger.warning(
                "plot-from-results: id=%s не найден в id/combination_id/rank, использован 1-based номер строки=%s",
                selected_id,
                selected_id,
            )
    else:
        sorted_frame = frame.sort_values(["profit_factor", "trades_count"], ascending=[False, False], na_position="last")
        selected_row = sorted_frame.iloc[0]

    logger.info(
        "plot-from-results: использованы параметры из %s (pf=%s, trades_count=%s, id=%s)",
        csv_path,
        selected_row.get("profit_factor", "n/a"),
        selected_row.get("trades_count", "n/a"),
        selected_id_raw if selected_id_raw is not None else "best",
    )
    return selected_row

def _run_with_logging(command_name: str, config: AppConfig, body: Callable[[], int]) -> int:
    logger = get_logger(
        command_name,
        level=config.backtest.log_level,
        logs_dir=config.backtest.logs_dir,
    )
    logger.info(f"{command_name}: старт")
    try:
        code = body()
        logger.info(f"{LOG_MSG_TASK_COMPLETED % command_name} (код={code})")
        return code
    except Exception as exc:
        logger.exception(f"{command_name}: ошибка: {exc}")
        return 1


def _build_fetch_stack(config: AppConfig) -> tuple[MarketDataFetcher, CcxtFuturesClient, CoinGeckoClient]:
    market_caps_cache_path = config.backtest.cache_dir / "market_caps.parquet"
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
    market_client = CoinGeckoClient(
        api_key=config.fetch.coingecko_api_key,
        cache_path=market_caps_cache_path,
        retry_attempts=config.backtest.retry_attempts,
        retry_backoff_seconds=config.backtest.retry_backoff_seconds,
        min_request_interval_seconds=config.fetch.coingecko_min_request_interval_seconds,
    )
    return (
        MarketDataFetcher(
            ohlcv_fetcher=ohlcv_fetcher,
            oi_fetcher=oi_fetcher,
            market_data_client=market_client,
            retry_attempts=config.backtest.retry_attempts,
            retry_backoff_seconds=config.backtest.retry_backoff_seconds,
            log_level=config.backtest.log_level,
            logs_dir=config.backtest.logs_dir,
        ),
        exchange_client,
        market_client,
    )


def _resolve_symbols(
        exchange_client: CcxtFuturesClient,
        market_client: CoinGeckoClient,
        top_n: int,
        min_volume_usd: float,
        logger: Logger,
        coingecko_volume_batch_size: int,
        ignore_coingecko: bool,
        cache_dir: Path,
        liquidity_timeframe: Timeframe,
        futures_symbols_raw: list[str] | None = None,
) -> tuple[list[str], dict[str, dict[str, object]]]:
    futures_symbols_raw = futures_symbols_raw or exchange_client.get_futures_symbols()
    liquidity_quality_by_symbol: dict[str, dict[str, object]] = {}
    futures_symbol_map = {
        normalize_symbol(symbol): symbol
        for symbol in futures_symbols_raw
    }
    exchange_symbols_normalized = sorted(futures_symbol_map)

    if ignore_coingecko:
        ranker = DailyVolumeRanker(cache_dir=cache_dir)
        symbols_raw = [futures_symbol_map[symbol] for symbol in exchange_symbols_normalized]
        avg_daily_volumes = ranker.calculate_avg_daily_volume_usd(
            symbols=symbols_raw,
            timeframe=liquidity_timeframe,
            logger=logger,
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
        newly_admitted_symbols: set[str] = set()

        try:
            ranked_metrics = exchange_client.get_futures_symbols_with_liquidity_metrics()
            for item in ranked_metrics:
                symbol_raw = str(item.get("symbol", ""))
                symbol = normalize_symbol(symbol_raw)
                if symbol not in futures_symbol_map:
                    continue

                quote_volume = float(item.get("quote_volume", 0.0) or 0.0)
                liquidity_score = float(item.get("liquidity_score", 0.0) or 0.0)
                liquidity_score_by_symbol[symbol] = liquidity_score
                liquidity_quality_by_symbol[symbol_raw] = {
                    "liquidity_score": liquidity_score,
                    "quote_volume": quote_volume,
                    "trade_count_24h": int(item.get("trade_count_24h", 0) or 0),
                    "quality_flags": list(cast(list[object], item.get("quality_flags", []))),
                    "quality_metadata": dict(cast(dict[str, object], item.get("quality_metadata", {}))),
                }

                if symbol not in avg_daily_volumes_normalized and quote_volume >= min_volume_usd:
                    newly_admitted_symbols.add(symbol)
                    combined_volume_score_by_symbol[symbol] = quote_volume
        except Exception as exc:
            logger.warning(
                "подбор-символов: добор по метрикам ликвидности биржи недоступен (%s)",
                exc,
            )

        combined_symbols = set(liquid_symbols) | newly_admitted_symbols
        if not combined_symbols:
            fallback_symbols = exchange_symbols_normalized[:top_n]
            logger.info(
                "подбор-символов: CoinGecko отключен, пул ликвидности пуст → fallback на алфавитный top_n=%s",
                len(fallback_symbols),
            )
            return [futures_symbol_map[symbol] for symbol in fallback_symbols], liquidity_quality_by_symbol

        ranked_top_symbols = sorted(
            combined_symbols,
            key=lambda symbol: (
                combined_volume_score_by_symbol.get(symbol, 0.0),
                liquidity_score_by_symbol.get(symbol, 0.0),
                symbol,
            ),
            reverse=True,
        )[:top_n]

        logger.info(
            "подбор-символов: CoinGecko отключен (режим=cache+exchange-liquidity), всего на бирже=%s → в кэше с объёмом=%s → кэш прошёл фильтр ликвидности=%s → итоговый объединённый пул=%s → после top_n=%s",
            len(exchange_symbols_normalized),
            len(symbols_with_volume),
            len(liquid_symbols),
            len(combined_symbols),
            len(ranked_top_symbols),
        )
        logger.info(
            "подбор-символов: фильтр ликвидности по среднедневному объёму (мин_avg_daily_volume_usd=%.2f) исключено=%s",
            min_volume_usd,
            len(symbols_with_volume) - len(liquid_symbols),
        )
        logger.info(
            "подбор-символов: newly admitted symbols из биржевых метрик=%s",
            len(newly_admitted_symbols),
        )
        return [futures_symbol_map[symbol] for symbol in ranked_top_symbols], liquidity_quality_by_symbol

    market_caps_by_symbol = market_client.get_market_caps(exchange_symbols_normalized)
    ranked_symbols = sorted(
        exchange_symbols_normalized,
        key=lambda symbol: (market_caps_by_symbol.get(symbol, 0.0), symbol),
        reverse=True,
    )
    ranked_top_symbols = ranked_symbols[:top_n]

    volumes_by_symbol: dict[str, float] = {}
    symbols_skipped_by_api_errors = 0

    for start in range(0, len(ranked_top_symbols), coingecko_volume_batch_size):
        batch = ranked_top_symbols[start:start + coingecko_volume_batch_size]
        try:
            volumes_by_symbol.update(market_client.get_total_volumes(batch))
        except (RuntimeError, RetryExhaustedError) as exc:
            symbols_skipped_by_api_errors += len(batch)
            logger.warning(
                "подбор-символов: ошибка внешнего API при получении объёма батча (%s символов, first=%s): %s; символы будут исключены",
                len(batch),
                batch[0] if batch else "n/a",
                exc,
            )
            continue
        except Exception as exc:
            root_cause = exc.__cause__
            if isinstance(root_cause, RetryExhaustedError):
                symbols_skipped_by_api_errors += len(batch)
                logger.warning(
                    "подбор-символов: ошибка внешнего API при получении объёма батча (%s символов, first=%s): %s; символы будут исключены",
                    len(batch),
                    batch[0] if batch else "n/a",
                    exc,
                )
                continue
            raise

    symbols_with_volume = [symbol for symbol in ranked_top_symbols if symbol in volumes_by_symbol]

    liquid_symbols: list[str] = []
    for symbol in symbols_with_volume:
        total_volume = volumes_by_symbol.get(symbol, 0.0)
        if total_volume >= min_volume_usd:
            liquid_symbols.append(symbol)

    excluded_by_liquidity = len(symbols_with_volume) - len(liquid_symbols)
    logger.info(
        "подбор-символов: всего на бирже=%s → после ранжирования top_n=%s → после фильтра ликвидности=%s",
        len(exchange_symbols_normalized),
        len(ranked_top_symbols),
        len(liquid_symbols),
    )
    logger.info(
        "подбор-символов: пропущено символов из-за ошибок внешнего API=%s",
        symbols_skipped_by_api_errors,
    )
    logger.info(
        "подбор-символов: фильтр ликвидности (мин_объем_usd=%.2f) исключено=%s итоговых_символов=%s",
        min_volume_usd,
        excluded_by_liquidity,
        len(liquid_symbols),
    )
    return [futures_symbol_map[symbol] for symbol in liquid_symbols], liquidity_quality_by_symbol


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
    command_name: str,
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
            "%s: сводка загрузки всего=%s успешно=%s с ошибками=%s доля_ошибок=%.2f%%",
            command_name,
            summary.total_symbols,
            summary.success_symbols,
            summary.failed_symbols,
            summary.failed_ratio * 100,
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
        "loaded": "Загружено %s монет.",
        "updated": "Обновлено %s монет.",
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


def _fetch_data_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("fetch-data", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)

    if args.top_n is not None and args.top_n <= 0:
        logger.error("fetch-data: --top-n must be > 0")
        return 1
    if args.days <= 0:
        logger.error("fetch-data: --days must be > 0")
        return 1

    fetcher, exchange_client, market_client = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    all_futures_count = len(futures_symbols)
    min_volume_usd = args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd
    ignore_coingecko = args.ignore_coingecko if args.ignore_coingecko is not None else config.fetch.ignore_coingecko
    market_client.set_skip_invalid_coin_id_filter(ignore_coingecko)
    top_n = args.top_n if args.top_n is not None else all_futures_count
    symbols, liquidity_quality_by_symbol = _resolve_symbols(
        exchange_client,
        market_client,
        top_n=top_n,
        min_volume_usd=min_volume_usd,
        logger=logger,
        coingecko_volume_batch_size=config.fetch.coingecko_volume_batch_size,
        ignore_coingecko=ignore_coingecko,
        cache_dir=config.backtest.cache_dir,
        liquidity_timeframe=config.fetch.timeframe,
        futures_symbols_raw=futures_symbols,
    )
    logger.info(
        "загрузка-данных: найдено фьючерсов=%s выбрано_символов=%s (режим_подбора=%s)",
        all_futures_count,
        len(symbols),
        "coingecko-disabled" if ignore_coingecko else "coingecko-enabled",
    )
    if not symbols:
        liquidity_quality_by_symbol = {}
        logger.info("загрузка-данных: не найдено символов для загрузки")
        return 0

    start_timestamp_ms, end_timestamp_ms = _fetch_period(config, args.days, getattr(args, "end_timestamp_ms", None))
    failed_symbols: set[str] = set()
    fetch_summaries: dict[Timeframe, FetchSummary] = {}

    def _fetch_for_timeframe(
        requested_timeframe: Timeframe,
        symbols_to_fetch: list[str],
        *,
        emit_log: bool = True,
    ) -> None:
        logger.info(
            "загрузка-данных: сбор кэша для TF=%s (символов=%s)",
            requested_timeframe.value,
            len(symbols_to_fetch),
        )
        result = fetcher.fetch_all(
            symbols=symbols_to_fetch,
            timeframe=requested_timeframe,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
        )
        enriched_ohlcv = _attach_liquidity_quality_metadata(result.ohlcv, liquidity_quality_by_symbol)
        result.ohlcv.clear()
        result.ohlcv.update(enriched_ohlcv)
        enriched_open_interest = _attach_liquidity_quality_metadata(result.open_interest, liquidity_quality_by_symbol)
        result.open_interest.clear()
        result.open_interest.update(enriched_open_interest)

        fetch_summaries[requested_timeframe] = _log_fetch_summary(
            f"fetch-data[{requested_timeframe.value}]",
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

    primary_timeframe = config.fetch.timeframe
    _fetch_for_timeframe(primary_timeframe, symbols, emit_log=True)

    root_stage_status = "ok"
    followup_symbols = symbols
    if ignore_coingecko:
        liquidity_summary = fetch_summaries.get(primary_timeframe)
        skip_reason = _resolve_liquidity_skip_reason(
            liquidity_summary,
            config.fetch.liquidity_skip_error_ratio_threshold,
        )
        if skip_reason is None:
            followup_symbols, liquidity_quality_by_symbol = _resolve_symbols(
                exchange_client,
                market_client,
                top_n=top_n,
                min_volume_usd=min_volume_usd,
                logger=logger,
                coingecko_volume_batch_size=config.fetch.coingecko_volume_batch_size,
                ignore_coingecko=True,
                cache_dir=config.backtest.cache_dir,
                liquidity_timeframe=config.fetch.timeframe,
                futures_symbols_raw=futures_symbols,
            )
            logger.info(
                "загрузка-данных: применён пересчитанный список ликвидных символов после первичной загрузки (символов=%s)",
                len(followup_symbols),
            )
        else:
            root_stage_status = "ohlcv_cache_failed"
            logger.warning(
                "liquidity-skip: reason=%s timeframe=%s",
                skip_reason,
                primary_timeframe.value,
            )

    for timeframe in config.fetch.timeframes:
        if timeframe == primary_timeframe:
            continue
        _fetch_for_timeframe(timeframe, followup_symbols, emit_log=True)

    exit_code = _fetch_exit_code(len(failed_symbols))
    if root_stage_status == "ohlcv_cache_failed":
        exit_code = 2

    logger.info("fetch-data: status=%s exit_code=%s", root_stage_status, exit_code)
    _log_loaded_coins(logger, len(followup_symbols), "loaded")
    return exit_code


def _update_cache_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("update-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)

    if args.top_n is not None and args.top_n <= 0:
        logger.error("update-cache: --top-n must be > 0")
        return 1
    if args.days <= 0:
        logger.error("update-cache: --days must be > 0")
        return 1

    fetcher, exchange_client, market_client = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    all_futures_count = len(futures_symbols)
    min_volume_usd = args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd
    ignore_coingecko = args.ignore_coingecko if args.ignore_coingecko is not None else config.fetch.ignore_coingecko
    market_client.set_skip_invalid_coin_id_filter(ignore_coingecko)
    top_n = args.top_n if args.top_n is not None else all_futures_count
    symbols, liquidity_quality_by_symbol = _resolve_symbols(
        exchange_client,
        market_client,
        top_n=top_n,
        min_volume_usd=min_volume_usd,
        logger=logger,
        coingecko_volume_batch_size=config.fetch.coingecko_volume_batch_size,
        ignore_coingecko=ignore_coingecko,
        cache_dir=config.backtest.cache_dir,
        liquidity_timeframe=config.fetch.timeframe,
        futures_symbols_raw=futures_symbols,
    )
    logger.info(
        "обновление-кэша: найдено фьючерсов=%s отправлено в fetch_all=%s",
        all_futures_count,
        len(symbols),
    )
    if not symbols:
        logger.info("обновление-кэша: не найдено символов для обновления")
        return 0

    start_timestamp_ms, end_timestamp_ms = _fetch_period(config, args.days, getattr(args, "end_timestamp_ms", None))
    failed_symbols: set[str] = set()
    for timeframe in config.fetch.timeframes:
        logger.info("обновление-кэша: сбор кэша для TF=%s", timeframe.value)
        result = fetcher.fetch_all(symbols=symbols, timeframe=timeframe, start_timestamp_ms=start_timestamp_ms, end_timestamp_ms=end_timestamp_ms)
        enriched_ohlcv = _attach_liquidity_quality_metadata(result.ohlcv, liquidity_quality_by_symbol)
        result.ohlcv.clear()
        result.ohlcv.update(enriched_ohlcv)
        enriched_open_interest = _attach_liquidity_quality_metadata(result.open_interest, liquidity_quality_by_symbol)
        result.open_interest.clear()
        result.open_interest.update(enriched_open_interest)
        _log_fetch_summary(f"update-cache[{timeframe.value}]", logger, len(symbols), result.failed_symbols_count)
        failed_symbols.update(
            symbol
            for symbol in symbols
            if (symbol in result.ohlcv and not result.ohlcv[symbol].success)
            or (symbol in result.open_interest and not result.open_interest[symbol].success)
            or isinstance(result.market_caps.market_caps.get(symbol), str)
        )

    exit_code = _fetch_exit_code(len(failed_symbols))
    _log_loaded_coins(logger, len(symbols), "updated")
    return exit_code


def _run_backtest_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-backtest", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    strategy_id = _resolve_strategy_id(args)
    config.strategy.strategy_id = strategy_id
    config.backtest.results_dir = _resolve_results_dir_for_strategy(config.backtest.results_dir, strategy_id)

    if strategy_id == "bee_bite":
        profile_runtime = get_bee_bite_runtime(config.strategy.bee_bite_profile)
        config.strategy.bee_bite_grid_mode = parse_bee_bite_grid_mode(
            getattr(args, "bee_bite_grid", None),
            default=config.strategy.bee_bite_grid_mode,
        )
        config.strategy.bee_bite_reclaim_mode = parse_bee_bite_reclaim_mode(
            getattr(args, "bee_bite_reclaim_mode", None),
            default=profile_runtime.reclaim_mode,
        )
        config.strategy.bee_bite_retest_mode = parse_bee_bite_retest_mode(
            getattr(args, "bee_bite_retest_mode", None),
            default=profile_runtime.retest_mode,
        )
        config.strategy.bee_bite_cooldown_hours = (
            int(getattr(args, "bee_bite_cooldown_hours", None))
            if getattr(args, "bee_bite_cooldown_hours", None) is not None
            else profile_runtime.cooldown_hours
        )
        config.strategy.bee_bite_max_age_range_hours = (
            int(getattr(args, "bee_bite_max_age_range_hours", None))
            if getattr(args, "bee_bite_max_age_range_hours", None) is not None
            else profile_runtime.max_age_range_hours
        )
        validate_bee_bite_runtime(
            profile_id=config.strategy.bee_bite_profile,
            grid_mode=config.strategy.bee_bite_grid_mode,
            top_n=getattr(args, "top_n", None),
            reclaim_mode=config.strategy.bee_bite_reclaim_mode,
            retest_mode=config.strategy.bee_bite_retest_mode,
            cooldown_hours=config.strategy.bee_bite_cooldown_hours,
            max_age_range_hours=config.strategy.bee_bite_max_age_range_hours,
        )
        config.strategy.bee_bite_portfolio_top_n = getattr(args, "top_n", None)
    levels_timeframe = _resolve_timeframe(
        getattr(args, "levels_tf", None),
        fallback=config.strategy.levels_timeframe,
        argument_name="--levels-tf",
    )
    entry_timeframe = _resolve_timeframe(
        getattr(args, "entry_tf", None),
        fallback=config.strategy.entry_timeframe,
        argument_name="--entry-tf",
    )
    logger.info(
        "запуск-бэктеста: явный запуск, уровни: %s, входы: %s",
        levels_timeframe.value,
        entry_timeframe.value,
    )

    preparer = DataPreparer(config.backtest.cache_dir)
    symbols = args.symbols or preparer.list_symbols(entry_timeframe)
    if not symbols:
        logger.info("запуск-бектеста: нет данных в кэше")
        return 0

    symbols_before_ranking = len(symbols)
    top_n = getattr(args, "top_n", None)
    pre_rank_enabled = top_n is not None and top_n > 0
    pre_filter_active = strategy_id == "bee_bite" or pre_rank_enabled
    ranked_symbols: list[tuple[str, float]] = []
    rejected_symbols_count = 0
    preloaded_levels_frames: dict[str, pd.DataFrame] = {}
    preloaded_entry_frames: dict[str, pd.DataFrame] = {}
    pre_rank_started_at = time.perf_counter()
    if strategy_id == "bee_bite":
        stage1_selector = BeeBiteStage1Selector()
        stage1_results: list[BeeBiteStage1Result] = []
        stage1_reason_counts: Counter[str] = Counter()
        for symbol in symbols:
            entry_15m_frame = preparer.load_symbol_data(symbol, Timeframe.M15)
            preloaded_entry_frames[symbol] = entry_15m_frame
            if levels_timeframe == Timeframe.M15:
                preloaded_levels_frames[symbol] = entry_15m_frame

            stage1_result = stage1_selector.evaluate_symbol(symbol=symbol, frame=entry_15m_frame)
            stage1_reason_counts[stage1_result.reason] += 1
            if stage1_result.passed:
                stage1_results.append(stage1_result)

        stage1_results.sort(
            key=lambda item: (
                float(item.rolling_volume_usdt or 0.0),
                float(item.pump_percent or 0.0),
                float(item.retain_ratio or 0.0),
                item.symbol,
            ),
            reverse=True,
        )
        selected_stage1_results = stage1_results[:top_n] if pre_rank_enabled else stage1_results
        symbols = [item.symbol for item in selected_stage1_results]
        ranked_symbols_count = len(stage1_results)
        rejected_symbols_count = symbols_before_ranking - ranked_symbols_count
        top_n_applied = top_n if pre_rank_enabled else "не применялся"
        preview = selected_stage1_results[:10]
        top_preview_text = ", ".join(
            (
                f"{item.symbol} vol24h={float(item.rolling_volume_usdt or 0.0):.2f} "
                f"pump={float(item.pump_percent or 0.0) * 100:.2f}% "
                f"retain={float(item.retain_ratio or 0.0) * 100:.2f}%"
            )
            for item in preview
        )
        if not top_preview_text:
            top_preview_text = "пусто"
        logger.info(
            "запуск-бэктеста: bee_bite stage1 symbols_total=%s passed=%s top_n=%s selected=%s reasons=%s",
            symbols_before_ranking,
            ranked_symbols_count,
            top_n_applied,
            len(symbols),
            dict(stage1_reason_counts),
        )
    elif pre_rank_enabled:
        for symbol in symbols:
            levels_frame = preparer.load_symbol_data(symbol, levels_timeframe)
            preloaded_levels_frames[symbol] = levels_frame
            if levels_frame.empty:
                logger.debug(
                    "запуск-бэктеста: символ %s исключён из pre-rank, причина=пустой levels_tf=%s",
                    symbol,
                    levels_timeframe.value,
                )
                rejected_symbols_count += 1
                continue
            if "volume" not in levels_frame.columns:
                logger.info(
                    "запуск-бэктеста: символ %s исключён из pre-rank, причина=нет колонки volume на levels_tf=%s",
                    symbol,
                    levels_timeframe.value,
                )
                rejected_symbols_count += 1
                continue

            volume_numeric = pd.Series(pd.to_numeric(levels_frame["volume"], errors="coerce"), index=levels_frame.index)
            volume_series = volume_numeric.dropna()
            if volume_series.empty:
                logger.debug(
                    "запуск-бэктеста: символ %s исключён из pre-rank, причина=нет валидного volume на levels_tf=%s",
                    symbol,
                    levels_timeframe.value,
                )
                rejected_symbols_count += 1
                continue

            ranked_symbols.append((symbol, float(volume_series.mean())))

        ranked_symbols.sort(key=lambda item: item[1], reverse=True)
        ranked_symbols_count = len(ranked_symbols)
        selected_ranked_symbols = ranked_symbols[:top_n]
        symbols = [symbol for symbol, _ in selected_ranked_symbols]
        top_n_applied = top_n

        preview = selected_ranked_symbols[:10]
        top_preview_text = ", ".join(
            f"{symbol} avg_volume={avg_volume:.4f}"
            for symbol, avg_volume in preview
        )
        if not top_preview_text:
            top_preview_text = "пусто"
    else:
        ranked_symbols_count = 0
        symbols = list(symbols)
        top_n_applied = "не применялся"
        top_preview_text = "pre-rank отключён"

    pre_rank_elapsed_seconds = time.perf_counter() - pre_rank_started_at
    logger.info(
        "запуск-бэктеста: pre-rank время=%.3fs enabled=%s",
        pre_rank_elapsed_seconds,
        pre_filter_active,
    )
    if strategy_id == "bee_bite":
        logger.info(
            "запуск-бэктеста: bee_bite stage1 total=%s passed=%s rejected=%s top_n=%s selected=%s",
            symbols_before_ranking,
            ranked_symbols_count,
            rejected_symbols_count,
            top_n_applied,
            len(symbols),
        )
        if not pre_rank_enabled:
            logger.info(
                "запуск-бэктеста: bee_bite stage1 top_n не задан, используется весь stage1-отбор (%s)",
                len(symbols),
            )
    else:
        logger.info(
            "запуск-бэктеста: pre-rank symbols_total=%s валидный_volume_levels_tf=%s rejected=%s top_n=%s выбрано_после_отсечения=%s",
            symbols_before_ranking,
            ranked_symbols_count,
            rejected_symbols_count,
            top_n_applied,
            len(symbols),
        )
        if not pre_rank_enabled:
            logger.info(
                "запуск-бэктеста: pre-rank top_n не задан или <= 0, используется исходный список символов (%s)",
                len(symbols),
            )
    logger.info("запуск-бэктеста: pre-rank top-list: %s", top_preview_text)

    if not symbols:
        if strategy_id == "bee_bite":
            logger.info("запуск-бэктеста: ранний выход, после bee_bite stage1 список символов пуст")
        elif ranked_symbols_count == 0:
            logger.info(
                "запуск-бэктеста: ранний выход, нет символов с валидным объёмом на levels_tf=%s",
                levels_timeframe.value,
            )
        else:
            logger.info("запуск-бэктеста: ранний выход, после применения top_n=%s список символов пуст", top_n)
        return 0

    symbol_frames: dict[str, SymbolMtfFrames] = {}
    symbols_total = len(symbols)
    symbols_prepare_started_at = time.perf_counter()
    symbols_missing_levels_tf = 0
    symbols_missing_entry_tf = 0
    symbols_used = 0
    for idx, symbol in enumerate(symbols, start=1):
        levels_frame = preloaded_levels_frames.get(symbol)
        if levels_frame is None:
            levels_frame = preparer.load_symbol_data(symbol, levels_timeframe)
        entry_frame: pd.DataFrame | None = (
            preloaded_entry_frames.get(symbol)
            if entry_timeframe == Timeframe.M15
            else None
        )
        if levels_timeframe == entry_timeframe:
            entry_frame = levels_frame if entry_frame is None else entry_frame
        elif entry_frame is None:
            entry_frame = preparer.load_symbol_data(symbol, entry_timeframe)
        if levels_frame.empty:
            symbols_missing_levels_tf += 1
        if entry_frame.empty:
            symbols_missing_entry_tf += 1
        if levels_frame.empty or entry_frame.empty:
            continue
        symbols_used += 1
        symbol_frames[symbol] = SymbolMtfFrames(
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            levels_frame=levels_frame,
            entry_frame=entry_frame,
        )

        if idx % _PROGRESS_LOG_EVERY == 0 or idx == symbols_total:
            elapsed_seconds = time.perf_counter() - symbols_prepare_started_at
            progress = (idx / symbols_total) * 100 if symbols_total else 0.0
            eta_seconds = (elapsed_seconds / idx) * (symbols_total - idx) if idx else 0.0
            logger.info(
                "анализ-кэша: подготовка-символов %s/%s (%.1f%%), eta=%ss",
                idx,
                symbols_total,
                progress,
                int(eta_seconds),
            )
    if not symbol_frames:
        logger.info("запуск-бектеста: не удалось подготовить данные")
        return 0

    strategy = build_strategy(config, logger)
    runner = BacktestRunner(
        config.backtest.results_dir,
        config.backtest.results_file_name,
        logger=logger,
    )
    symbols_used_ratio = symbols_used / symbols_total if symbols_total else 0.0
    logger.info(
        "запуск-бэктеста: сводка по символам всего=%s использовано=%s без_данных_levels_tf=%s без_данных_entry_tf=%s",
        symbols_total,
        symbols_used,
        symbols_missing_levels_tf,
        symbols_missing_entry_tf,
    )
    if symbols_total and symbols_used_ratio < 0.2:
        logger.warning(
            "запуск-бэктеста: используется только %.1f%% символов (%s из %s); результат бэктеста может быть нерепрезентативным",
            symbols_used_ratio * 100,
            symbols_used,
            symbols_total,
        )
    plot_from_results = _to_bool_flag(getattr(args, "plot_from_results", None), default=False)
    if plot_from_results:
        best_row = _load_plot_params_row_from_results(config, args, logger=logger, strategy_id=strategy_id)
        if best_row is None:
            return 1
        if not _plot_for_strategy(
            config=config,
            args=args,
            logger=logger,
            strategy_id=strategy_id,
            strategy=strategy,
            symbol_frames=symbol_frames,
            params_row=best_row,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            log_prefix="plot-from-results",
        ):
            return 1
        return 0

    results = runner.run(
        strategy,
        symbol_frames,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
    )
    summary = runner.build_summary(results)
    combinations_with_trades = int((results["trades_count"] > 0).sum()) if not results.empty else 0
    total_trades = int(results["trades_count"].sum()) if not results.empty else 0
    average_trades_per_combination = (
        total_trades / summary.total_combinations
        if summary.total_combinations
        else 0.0
    )
    median_trades_per_combination = float(results["trades_count"].median()) if not results.empty else 0.0
    logger.info(
        "запуск-бэктеста: всего=%s прибыльных=%s лучший_pf=%.4f комбинаций_со_сделками=%s сумма_сделок_по_сетке=%s среднее_сделок_на_комбинацию=%.4f медиана_сделок_на_комбинацию=%.4f",
        summary.total_combinations,
        summary.profitable_combinations,
        summary.best_pf,
        combinations_with_trades,
        total_trades,
        average_trades_per_combination,
        median_trades_per_combination,
    )
    if summary.best_pf == 0 and total_trades == 0:
        logger.warning(
            "запуск-бэктеста: отсутствуют сделки по всем комбинациям; проверьте достаточность истории для levels_tf=%s и соответствие таймфреймов в кэше (%s/%s)",
            levels_timeframe.value,
            levels_timeframe.value,
            entry_timeframe.value,
        )

    should_plot = _to_bool_flag(getattr(args, "plot", None), default=False)
    if should_plot:
        if results.empty:
            logger.warning("запуск-бэктеста: plot=true, но результаты пустые")
            return 0

        best_row = results.iloc[0]
        if not _plot_for_strategy(
            config=config,
            args=args,
            logger=logger,
            strategy_id=strategy_id,
            strategy=strategy,
            symbol_frames=symbol_frames,
            params_row=best_row,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            log_prefix="запуск-бэктеста: plot=true",
        ):
            return 1
    return 0


def _stage1_event_to_row(event: BeeBiteStage1Result) -> dict[str, object]:
    return {
        "symbol": event.symbol,
        "reason": event.reason,
        "sleep_start_timestamp": event.sleep_start_timestamp,
        "sleep_end_timestamp": event.sleep_end_timestamp,
        "pump_start_timestamp": event.pump_start_timestamp,
        "pump_peak_timestamp": event.pump_peak_timestamp,
        "stage1_confirmed_timestamp": event.stage1_confirmed_timestamp,
        "pump_base_price": event.pump_base_price,
        "pump_peak_price": event.pump_peak_price,
        "hold_price": event.hold_price,
        "lowest_after_pump": event.lowest_after_pump,
        "lowest_after_pump_timestamp": event.lowest_after_pump_timestamp,
        "pump_percent": event.pump_percent,
        "retain_ratio": event.retain_ratio,
        "rolling_volume_usdt": event.rolling_volume_usdt,
        "sleep_avg_volume_usdt": event.sleep_avg_volume_usdt,
        "post_pump_avg_volume_usdt": event.post_pump_avg_volume_usdt,
        "post_pump_volume_ratio": event.post_pump_volume_ratio,
    }


def _review_stage1_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("review-stage1", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    logger.info("review-stage1: cache_dir=%s", config.backtest.cache_dir)
    results_dir = _resolve_results_dir_for_strategy(config.backtest.results_dir, "bee_bite")
    output_dir = results_dir / "stage1_review"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = Path(args.output) if getattr(args, "output", None) else output_dir / DEFAULT_STAGE1_EVENTS_OUTPUT_FILE
    plots_dir = output_dir / DEFAULT_STAGE1_PLOTS_DIR_NAME
    plot_limit = int(getattr(args, "plot_limit", 20) or 20)

    preparer = DataPreparer(config.backtest.cache_dir)
    symbols_raw = args.symbols or preparer.list_symbols(Timeframe.M15)
    symbols = [normalize_symbol(symbol) for symbol in symbols_raw]
    if not symbols:
        logger.info("review-stage1: нет данных в кэше для entry_tf=15m")
        return 0

    selector = BeeBiteStage1Selector()
    plotter = BeeBiteStage1Plotter()
    all_events: list[BeeBiteStage1Result] = []
    latest_event_by_symbol: dict[str, BeeBiteStage1Result] = {}
    frames_by_symbol: dict[str, pd.DataFrame] = {}
    reason_counts: Counter[str] = Counter()
    reason_samples: dict[str, list[str]] = {}
    empty_frame_symbols: list[str] = []
    populated_frame_symbols = 0
    min_rows: int | None = None
    max_rows: int | None = None

    for index, symbol in enumerate(symbols, start=1):
        frame = preparer.load_symbol_data(symbol, Timeframe.M15)
        frames_by_symbol[symbol] = frame
        rows_count = int(len(frame))
        if frame.empty:
            empty_frame_symbols.append(symbol)
        else:
            populated_frame_symbols += 1
            min_rows = rows_count if min_rows is None else min(min_rows, rows_count)
            max_rows = rows_count if max_rows is None else max(max_rows, rows_count)
        events = selector.detect_events(symbol=symbol, frame=frame)
        if events:
            all_events.extend(events)
            latest_event_by_symbol[symbol] = _select_primary_stage1_event(events)
        else:
            evaluation = selector.evaluate_symbol(symbol=symbol, frame=frame)
            reason_key = evaluation.reason if not evaluation.passed else "passed_now_but_no_historical_event"
            reason_counts[reason_key] += 1
            if len(reason_samples.get(reason_key, [])) < _STAGE1_REASON_SAMPLE_LIMIT:
                reason_samples.setdefault(reason_key, []).append(_format_stage1_reason_sample(evaluation, frame))

        if index % _PROGRESS_LOG_EVERY == 0 or index == len(symbols):
            logger.info(
                "review-stage1: progress=%s/%s symbols_with_events=%s events_total=%s populated_frames=%s empty_frames=%s",
                index,
                len(symbols),
                len(latest_event_by_symbol),
                len(all_events),
                populated_frame_symbols,
                len(empty_frame_symbols),
            )

    rows = [_stage1_event_to_row(event) for event in all_events]
    events_frame = pd.DataFrame(rows)
    if not events_frame.empty:
        events_frame = events_frame.sort_values(
            ["stage1_confirmed_timestamp", "pump_peak_timestamp", "symbol"],
            ascending=[False, False, True],
        ).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    events_frame.to_csv(output_path, index=False)

    latest_events = sorted(
        latest_event_by_symbol.values(),
        key=lambda item: (
            int(item.stage1_confirmed_timestamp or 0),
            int(item.pump_peak_timestamp or 0),
            item.symbol,
        ),
        reverse=True,
    )
    plots_built = 0
    for event in latest_events[:plot_limit]:
        frame = frames_by_symbol.get(event.symbol)
        if frame is None or frame.empty:
            continue
        output_file = plots_dir / f"{event.symbol.replace('/', '_')}_stage1.png"
        plotter.plot_event(frame=frame, event=event, output_path=output_file)
        plots_built += 1

    top_reasons = ", ".join(f"{reason}={count}" for reason, count in reason_counts.most_common())
    logger.info(
        "review-stage1: frames populated=%s empty=%s min_rows=%s max_rows=%s",
        populated_frame_symbols,
        len(empty_frame_symbols),
        min_rows or 0,
        max_rows or 0,
    )
    if top_reasons:
        logger.info("review-stage1: rejection_reasons %s", top_reasons)
    if empty_frame_symbols:
        logger.warning(
            "review-stage1: empty_frames symbols=%s",
            ", ".join(empty_frame_symbols[:10]),
        )
    for reason, samples in reason_samples.items():
        logger.info("review-stage1: reason=%s samples=%s", reason, " | ".join(samples))

    logger.info(
        "review-stage1: symbols=%s symbols_with_events=%s events=%s csv=%s plots=%s plots_dir=%s",
        len(symbols),
        len(latest_event_by_symbol),
        len(all_events),
        output_path,
        plots_built,
        plots_dir,
    )
    return 0


def _make_report_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("make-report", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    csv_path = Path(args.input) if args.input else config.backtest.results_dir / config.backtest.results_file_name
    if not csv_path.exists():
        logger.info(f"make-report: file not found: {csv_path}")
        return 1

    frame = pd.read_csv(csv_path)
    required_columns = [
        "bite_profile_id",
        "bite_grid_mode",
        "bite_lookback",
        "bite_volume_mult",
        "bite_retest_window_hours",
        "bite_min_rr",
        "bite_tp2_mult",
        "bite_confirmation_bars",
        "bite_reclaim_mode",
        "bite_retest_mode",
        "profit_factor",
        "pnl_percent",
        "win_rate",
        "trades_count",
        "max_dd",
        "sl_count",
        "be_count",
        "time_exit_profit_count",
        "tp1_be_count",
        "tp2_count",
    ]
    missing_columns = [column for column in required_columns if column not in frame.columns]
    if missing_columns:
        logger.error("make-report: missing required columns: %s", ", ".join(missing_columns))
        return 1

    if frame.empty:
        logger.info("make-report: results file is empty")
        return 1

    filtered = frame[
        (frame["trades_count"] >= REPORT_TRADES_COUNT_FILTER)
        & (frame["profit_factor"] > REPORT_PROFIT_FACTOR_FILTER)
    ].copy()
    filtered = filtered.sort_values("profit_factor", ascending=False)

    summary = BacktestSummary(
        total_combinations=int(len(frame)),
        profitable_combinations=int((frame["profit_factor"] > REPORT_PROFITABLE_PF_THRESHOLD).sum()),
        best_pf=round(float(frame["profit_factor"].max()), 4),
    )

    source = filtered if not filtered.empty else frame
    optimal_ranges = OptimalParameterRanges(
        bite_lookback=[int(source["bite_lookback"].min()), int(source["bite_lookback"].max())],
        bite_volume_mult=[round(float(source["bite_volume_mult"].min()), 4), round(float(source["bite_volume_mult"].max()), 4)],
        bite_min_rr=[round(float(source["bite_min_rr"].min()), 4), round(float(source["bite_min_rr"].max()), 4)],
        bite_tp2_mult=[round(float(source["bite_tp2_mult"].min()), 4), round(float(source["bite_tp2_mult"].max()), 4)],
    )

    distribution = TradeResultsDistribution(
        SL=int(source["sl_count"].sum()),
        BE=int(source["be_count"].sum()),
        TIME_EXIT_PROFIT=int(source["time_exit_profit_count"].sum()),
        TP1_BE=int(source["tp1_be_count"].sum()),
        TP2=int(source["tp2_count"].sum()),
    )

    profitable_variants = _build_profitable_variants(frame)
    ai_analysis_report = _build_ai_analysis_report(summary, profitable_variants)
    report = BacktestReport(
        summary=summary,
        optimal_ranges=optimal_ranges,
        trade_results_distribution=distribution,
        profitable_variants=profitable_variants,
        ai_analysis_report=ai_analysis_report,
    )

    output_path = Path(args.output) if args.output else config.backtest.results_dir / DEFAULT_REPORT_OUTPUT_FILE
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"make-report: saved {output_path}")
    return 0



def _build_profitable_variants(frame: pd.DataFrame) -> list[ProfitableVariant]:
    profitable = frame[frame["profit_factor"] > REPORT_PROFITABLE_PF_THRESHOLD].copy()
    if profitable.empty:
        return []

    profitable = profitable.sort_values(["profit_factor", "pnl_percent", "trades_count"], ascending=[False, False, False])

    variants: list[ProfitableVariant] = []
    for index, (_, row) in enumerate(profitable.iterrows(), start=1):
        variants.append(
            ProfitableVariant(
                rank=index,
                bite_profile_id=str(row["bite_profile_id"]),
                bite_grid_mode=str(row["bite_grid_mode"]),
                bite_lookback=int(row["bite_lookback"]),
                bite_volume_mult=round(float(row["bite_volume_mult"]), 4),
                bite_retest_window_hours=int(row["bite_retest_window_hours"]),
                bite_min_rr=round(float(row["bite_min_rr"]), 4),
                bite_tp2_mult=round(float(row["bite_tp2_mult"]), 4),
                bite_confirmation_bars=int(row["bite_confirmation_bars"]),
                bite_reclaim_mode=str(row["bite_reclaim_mode"]),
                bite_retest_mode=str(row["bite_retest_mode"]),
                profit_factor=round(float(row["profit_factor"]), 4),
                pnl_percent=round(float(row["pnl_percent"]), 4),
                win_rate=round(float(row["win_rate"]), 4),
                trades_count=int(row["trades_count"]),
                max_dd=round(float(row["max_dd"]), 4),
                sl_count=int(row["sl_count"]),
                be_count=int(row["be_count"]),
                tp1_be_count=int(row["tp1_be_count"]),
                tp2_count=int(row["tp2_count"]),
            )
        )
    return variants



def _build_ai_analysis_report(summary: BacktestSummary, variants: list[ProfitableVariant]) -> str:
    if not variants:
        return (
            "No setups with profit_factor > 1.0 were found. "
            "Increase history depth or relax bee_bite filters."
        )

    lines = [
        "# Bee Bite profitable combinations",
        f"Total combinations: {summary.total_combinations}",
        f"Profitable combinations (PF>1.0): {summary.profitable_combinations}",
        f"Best Profit Factor: {summary.best_pf}",
        "",
        "## Variants",
        "Format: rank | PF | PnL% | WinRate% | Trades | MaxDD% | params",
    ]

    for variant in variants:
        params = (
            f"profile={variant.bite_profile_id}, grid={variant.bite_grid_mode}, "
            f"lookback={variant.bite_lookback}, volume_mult={variant.bite_volume_mult}, "
            f"retest_window_h={variant.bite_retest_window_hours}, min_rr={variant.bite_min_rr}, "
            f"tp2_mult={variant.bite_tp2_mult}, confirmation_bars={variant.bite_confirmation_bars}, "
            f"reclaim_mode={variant.bite_reclaim_mode}, retest_mode={variant.bite_retest_mode}"
        )
        lines.append(
            f"{variant.rank} | {variant.profit_factor} | {variant.pnl_percent} | {variant.win_rate} | "
            f"{variant.trades_count} | {variant.max_dd} | {params}"
        )

    return "\n".join(lines)


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
        logger.info("проверка-качества: нет данных для проверки")
        return 0

    validator = DataValidator()
    gap_detector = GapDetector()

    issues_by_type: Counter[str] = Counter()
    issues_by_severity: Counter[str] = Counter()
    symbols_report: dict[str, QualitySymbolStats] = {}

    total_issues = 0
    total_gaps = 0
    for symbol in symbols:
        frame = preparer.load_symbol_data(symbol, config.fetch.timeframe)
        if frame.empty:
            logger.info(f"проверка-качества: {symbol} пропущен, пустой датасет")
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

        logger.info(
            f"проверка-качества: {symbol} проблемы={symbol_total_issues} пропуски={len(gaps)} "
            f"проблемы_oi={len(oi_quality_issues)}"
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

    logger.info(f"проверка-качества: итог проблемы={report.summary.issues_total} пропуски={report.summary.gaps_total}")
    logger.info(f"проверка-качества: отчет сохранен {output_path}")
    return 0


def _clear_cache_inner(config: AppConfig, args: argparse.Namespace) -> int:
    del args
    logger = get_logger("clear-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)

    cache_dir = config.backtest.cache_dir
    cache_dir_str = str(cache_dir).strip()
    if not cache_dir_str:
        logger.error("очистка-кэша: путь к директории кэша пустой, удаление отменено")
        return 1

    resolved_cache_dir = cache_dir.expanduser().resolve()
    home_dir = Path.home().resolve()
    if resolved_cache_dir == Path(resolved_cache_dir.anchor):
        logger.error(f"очистка-кэша: путь '{resolved_cache_dir}' указывает на корень ФС, удаление отменено")
        return 1

    if resolved_cache_dir == home_dir:
        logger.error(f"очистка-кэша: путь '{resolved_cache_dir}' указывает на домашнюю директорию, удаление отменено")
        return 1

    logger.info(f"очистка-кэша: удаление содержимого {resolved_cache_dir}")
    shutil.rmtree(resolved_cache_dir, ignore_errors=True)
    resolved_cache_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"очистка-кэша: директория пересоздана {resolved_cache_dir}")
    return 0



# endregion Приватные

# Публичные точки входа

def fetch_data(config: AppConfig, args: argparse.Namespace) -> int:
    """Запускает сценарий загрузки рыночных данных."""
    return _run_with_logging("fetch-data", config, lambda: _fetch_data_inner(config, args))


def update_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Обновляет локальный кэш данных."""
    return _run_with_logging("update-cache", config, lambda: _update_cache_inner(config, args))


def run_backtest(config: AppConfig, args: argparse.Namespace) -> int:
    """Запускает бэктест по текущей конфигурации."""
    return _run_with_logging("run-backtest", config, lambda: _run_backtest_inner(config, args))


def make_report(config: AppConfig, args: argparse.Namespace) -> int:
    """Формирует итоговый отчёт по результатам."""
    return _run_with_logging("make-report", config, lambda: _make_report_inner(config, args))


def review_stage1(config: AppConfig, args: argparse.Namespace) -> int:
    """Находит historical stage-1 события и сохраняет review-артефакты."""
    return _run_with_logging("review-stage1", config, lambda: _review_stage1_inner(config, args))


def check_quality(config: AppConfig, args: argparse.Namespace) -> int:
    """Проверяет качество и целостность данных."""
    return _run_with_logging("check-quality", config, lambda: _check_quality_inner(config, args))


def clear_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Очищает директорию локального кэша и пересоздаёт её."""
    return _run_with_logging("clear-cache", config, lambda: _clear_cache_inner(config, args))



