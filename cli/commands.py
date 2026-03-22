"""Модуль проекта."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter
from dataclasses import asdict, dataclass
from logging import Logger
from pathlib import Path
from typing import Callable, TypeVar, cast

import numpy as np
import pandas as pd

from config import AppConfig
from constants import (
    BEE_BITE_STAGE4_SLIPPAGE_RATE,
    BEE_BITE_STAGE4_TAKER_FEE_RATE,
    DEFAULT_BACKTEST_OUTPUT_FILE,
    DEFAULT_FAILED_PLOTS_DIR_NAME,
    DEFAULT_STAGE1_EVENTS_OUTPUT_FILE,
    DEFAULT_STAGE1_PLOTS_DIR_NAME,
    DEFAULT_STAGE2_EVENTS_OUTPUT_FILE,
    DEFAULT_STAGE2_PLOTS_DIR_NAME,
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
from data.clients.noop_market_data_client import NoOpMarketDataClient
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.fetchers.market_data_fetcher import MarketDataFetcher
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from data.liquidity.bee_bite_stage1_plotter import BeeBiteStage1Plotter
from data.liquidity.bee_bite_stage2_plotter import BeeBiteStage2Plotter
from data.liquidity.bee_bite_stage3_plotter import BeeBiteStage3Plotter
from data.liquidity.bee_bite_stage4_plotter import BeeBiteStage4Plotter
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
    BeeBiteStage4PostmortemParams,
    BeeBiteStage2Detector,
    BeeBiteStage2Result,
    BeeBiteStage3Detector,
    BeeBiteStage3Result,
    get_bee_bite_stage4_postmortem_grid,
    get_bee_bite_runtime,
    parse_bee_bite_grid_mode,
    parse_bee_bite_profile_id,
    parse_bee_bite_reclaim_mode,
    parse_bee_bite_retest_mode,
    validate_bee_bite_runtime,
)
from strategy.bee_bite.stage3_rules import resolve_stage2_hold_price
from strategy.factory import build_strategy
from utils.logger import get_logger
from utils.symbols import normalize_symbol
from vectorbt_runner import BacktestRunner, DataPreparer, SymbolMtfFrames

# region Приватные

_PROGRESS_LOG_EVERY = 100
_STAGE1_REASON_SAMPLE_LIMIT = 3
_DEFAULT_STAGE3_EVENTS_OUTPUT_FILE = "stage3_events.csv"
_DEFAULT_STAGE3_PLOTS_DIR_NAME = "stage3_plots"
_DEFAULT_STAGE23_BACKTEST_OUTPUT_FILE = "stage23_backtest.csv"
_DEFAULT_STAGE4_POSTMORTEM_OUTPUT_FILE = "stage4_postmortem.csv"
_DEFAULT_STAGE4_GRID_OVERVIEW_OUTPUT_FILE = "stage4_grid_overview.csv"
_DEFAULT_STAGE4_REPORT_OUTPUT_FILE = "stage4_report.md"
_DEFAULT_STAGE4_SYMBOL_TIMEOUT_SECONDS = 120
_TItem = TypeVar("_TItem")


@dataclass(slots=True)
class _Stage1Regime:
    symbol: str
    regime_index: int
    events: list[BeeBiteStage1Result]
    primary_event: BeeBiteStage1Result
    first_event: BeeBiteStage1Result
    last_event: BeeBiteStage1Result
    regime_end_timestamp: int
    regime_end_reason: str


def _format_eta_compact(total_seconds: float) -> str:
    seconds = max(0, int(total_seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@dataclass(slots=True)
class _Stage23ReviewCandidate:
    symbol: str
    regime: _Stage1Regime
    stage1_event: BeeBiteStage1Result
    final_stage2_result: BeeBiteStage2Result
    final_stage3_result: BeeBiteStage3Result
    frame: pd.DataFrame
    snapshots: list["_Stage23EvolutionSnapshot"] | None = None


@dataclass(slots=True)
class _Stage23EvolutionSnapshot:
    order: int
    timestamp: int
    dynamic_stage2_result: BeeBiteStage2Result
    reference_stage2_result: BeeBiteStage2Result | None
    stage3_result: BeeBiteStage3Result


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
        timestamps = pd.Series(pd.to_numeric(frame["timestamp"], errors="coerce")).dropna()
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
    regimes = _group_stage1_events_into_regimes(events)
    if not regimes:
        raise ValueError("stage-1 primary event selection requires at least one event")
    return regimes[0].primary_event


def _group_stage1_events_into_regimes(events: list[BeeBiteStage1Result]) -> list[_Stage1Regime]:
    sorted_events = sorted(
        events,
        key=lambda item: (
            int(item.pump_start_timestamp or 0),
            int(item.pump_peak_timestamp or 0),
            int(item.stage1_confirmed_timestamp or 0),
        ),
    )
    if not sorted_events:
        return []

    grouped_events: list[list[BeeBiteStage1Result]] = []
    current_group: list[BeeBiteStage1Result] = []
    current_regime_peak_ms = 0
    for event in sorted_events:
        pump_start_ms = int(event.pump_start_timestamp or 0)
        pump_peak_ms = int(event.pump_peak_timestamp or pump_start_ms)
        if not current_group:
            current_group = [event]
            current_regime_peak_ms = pump_peak_ms
            continue

        if pump_start_ms <= current_regime_peak_ms:
            current_group.append(event)
            current_regime_peak_ms = max(current_regime_peak_ms, pump_peak_ms)
            continue

        grouped_events.append(current_group)
        current_group = [event]
        current_regime_peak_ms = pump_peak_ms
    if current_group:
        grouped_events.append(current_group)

    regimes: list[_Stage1Regime] = []
    for regime_index, regime_events in enumerate(grouped_events, start=1):
        ordered_by_detection = sorted(
            regime_events,
            key=lambda item: (
                int(item.stage1_confirmed_timestamp or 0),
                int(item.pump_peak_timestamp or 0),
                int(item.pump_start_timestamp or 0),
            ),
        )
        first_event = ordered_by_detection[0]
        last_event = ordered_by_detection[-1]
        regimes.append(
            _Stage1Regime(
                symbol=first_event.symbol,
                regime_index=regime_index,
                events=regime_events,
                primary_event=first_event,
                first_event=first_event,
                last_event=last_event,
                regime_end_timestamp=int(last_event.stage1_confirmed_timestamp or 0),
                regime_end_reason="unresolved",
            )
        )
    return regimes


def _resolve_stage1_regime_ends(*, frame: pd.DataFrame, regimes: list[_Stage1Regime]) -> list[_Stage1Regime]:
    if frame.empty or not regimes:
        return regimes

    prepared = frame.loc[:, ["timestamp", "open", "high", "low", "close"]].copy()
    for column in prepared.columns:
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close"])
    prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    if prepared.empty:
        return regimes

    timestamps = prepared["timestamp"].astype("int64").to_numpy()
    opens = prepared["open"].astype("float64").to_numpy()
    highs = prepared["high"].astype("float64").to_numpy()
    lows = prepared["low"].astype("float64").to_numpy()
    closes = prepared["close"].astype("float64").to_numpy()
    body_highs = np.maximum(opens, closes)
    body_sizes = np.abs(closes - opens)
    upper_wicks = highs - body_highs
    effective_highs = np.where(upper_wicks > body_sizes, body_highs, highs)
    timestamp_to_index = {int(timestamp): idx for idx, timestamp in enumerate(timestamps)}

    resolved_regimes: list[_Stage1Regime] = []
    for idx, regime in enumerate(regimes):
        default_end_ts = int(regime.last_event.stage1_confirmed_timestamp or timestamps[-1])
        resolved_end_ts = default_end_ts
        resolved_reason = "last_detected"

        start_scan_idx = timestamp_to_index.get(default_end_ts)
        if start_scan_idx is None:
            resolved_regimes.append(regime)
            continue
        peak_scan_idx = timestamp_to_index.get(int(regime.last_event.pump_peak_timestamp or 0), start_scan_idx)

        next_regime_start_ts: int | None = None
        if idx + 1 < len(regimes):
            next_regime_start_ts = int(regimes[idx + 1].first_event.pump_start_timestamp or 0)

        hold_price = float(
            resolve_stage2_hold_price(
                high_pump=regime.last_event.pump_peak_price,
                low_before_pump=(
                    regime.last_event.hold_base_price
                    if regime.last_event.hold_base_price is not None
                    else regime.last_event.pump_base_price
                ),
            )
            or 0.0
        )
        pump_peak_price = float(regime.last_event.pump_peak_price or 0.0)
        scan_end_idx = len(timestamps) - 1
        if next_regime_start_ts is not None and next_regime_start_ts in timestamp_to_index:
            scan_end_idx = timestamp_to_index[next_regime_start_ts]

        for scan_idx in range(start_scan_idx + 1, scan_end_idx + 1):
            timestamp_ms = int(timestamps[scan_idx])
            if next_regime_start_ts is not None and timestamp_ms >= next_regime_start_ts:
                resolved_end_ts = timestamp_ms
                resolved_reason = "next_regime_started"
                break
            if hold_price > 0.0 and float(lows[scan_idx]) < hold_price:
                resolved_end_ts = timestamp_ms
                resolved_reason = "dumped_below_hold"
                break
            if pump_peak_price > 0.0 and _is_significant_regime_breakout(
                effective_highs=effective_highs,
                highs=highs,
                lows=lows,
                peak_price=pump_peak_price,
                peak_idx=peak_scan_idx,
                breakout_idx=scan_idx,
            ):
                resolved_end_ts = timestamp_ms
                resolved_reason = "new_high_after_regime"
                break
        else:
            if next_regime_start_ts is not None:
                resolved_end_ts = int(next_regime_start_ts)
                resolved_reason = "next_regime_started"

        resolved_regimes.append(
            _Stage1Regime(
                symbol=regime.symbol,
                regime_index=regime.regime_index,
                events=regime.events,
                primary_event=regime.primary_event,
                first_event=regime.first_event,
                last_event=regime.last_event,
                regime_end_timestamp=resolved_end_ts,
                regime_end_reason=resolved_reason,
            )
        )
    return resolved_regimes


def _resolve_stage2_analysis_end_timestamp(*, frame: pd.DataFrame, regime: _Stage1Regime) -> int:
    if frame.empty or regime.regime_end_reason == "last_detected":
        return int(regime.regime_end_timestamp)

    prepared = frame.loc[:, ["timestamp"]].copy()
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp"])
    prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    if prepared.empty:
        return int(regime.regime_end_timestamp)

    timestamps = prepared["timestamp"].astype("int64").to_numpy()
    matches = np.where(timestamps == int(regime.regime_end_timestamp))[0]
    if matches.size == 0:
        return int(regime.regime_end_timestamp)
    end_idx = int(matches[-1])
    if end_idx <= 0:
        return int(regime.regime_end_timestamp)
    return int(timestamps[end_idx - 1])


_STAGE23_POST_REGIME_EXTENSION_MS = 24 * 60 * 60 * 1000


def _resolve_stage23_analysis_end_timestamp(
    *,
    frame: pd.DataFrame,
    timeframe: Timeframe,
    regime: _Stage1Regime,
) -> int:
    if frame.empty:
        return int(regime.regime_end_timestamp)

    prepared = frame.loc[:, ["timestamp"]].copy()
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp"])
    prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    if prepared.empty:
        return int(regime.regime_end_timestamp)

    timestamps = prepared["timestamp"].astype("int64").to_numpy()
    frame_end_timestamp = int(timestamps[-1])
    regime_end_timestamp = int(regime.regime_end_timestamp)
    capped_end_timestamp = min(
        frame_end_timestamp,
        regime_end_timestamp + _STAGE23_POST_REGIME_EXTENSION_MS,
    )
    capped_end_matches = np.where(timestamps <= capped_end_timestamp)[0]
    if capped_end_matches.size == 0:
        return regime_end_timestamp

    resolved_end_timestamp = int(timestamps[int(capped_end_matches[-1])])
    min_required_end_timestamp = regime_end_timestamp + timeframe.to_milliseconds()
    if resolved_end_timestamp < min_required_end_timestamp:
        return min(frame_end_timestamp, max(regime_end_timestamp, min_required_end_timestamp))
    return resolved_end_timestamp


def _is_significant_regime_breakout(
    *,
    effective_highs: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    peak_price: float,
    peak_idx: int,
    breakout_idx: int,
) -> bool:
    bars_since_peak = breakout_idx - peak_idx
    breakout_high = float(highs[breakout_idx]) if bars_since_peak <= 2 else float(effective_highs[breakout_idx])
    breakout_excess = breakout_high - peak_price
    if breakout_excess <= 0.0:
        return False
    candle_sizes = highs[peak_idx : breakout_idx + 1] - lows[peak_idx : breakout_idx + 1]
    mean_candle_size = float(np.mean(candle_sizes)) if candle_sizes.size > 0 else 0.0
    if bars_since_peak <= 2:
        return breakout_excess >= max(mean_candle_size * 0.1, 1e-12)
    return breakout_excess >= max(mean_candle_size, 1e-12)



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


def _build_fetch_stack(config: AppConfig) -> tuple[MarketDataFetcher, CcxtFuturesClient]:
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
    return (
        MarketDataFetcher(
            ohlcv_fetcher=ohlcv_fetcher,
            oi_fetcher=oi_fetcher,
            market_data_client=NoOpMarketDataClient(),
            retry_attempts=config.backtest.retry_attempts,
            retry_backoff_seconds=config.backtest.retry_backoff_seconds,
            log_level=config.backtest.log_level,
            logs_dir=config.backtest.logs_dir,
        ),
        exchange_client,
    )


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
    futures_symbol_map = {
        normalize_symbol(symbol): symbol
        for symbol in futures_symbols_raw
    }
    exchange_symbols_normalized = sorted(futures_symbol_map)

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
                'trade_count_24h': int(item.get('trade_count_24h', 0) or 0),
                'quality_flags': list(cast(list[object], item.get('quality_flags', []))),
                'quality_metadata': dict(cast(dict[str, object], item.get('quality_metadata', {}))),
            }

            if symbol not in avg_daily_volumes_normalized and quote_volume >= min_volume_usd:
                newly_admitted_symbols.add(symbol)
                combined_volume_score_by_symbol[symbol] = quote_volume
    except Exception as exc:
        logger.warning(
            '??????-????????: ????? ?? ???????? ??????????? ????? ?????????? (%s)',
            exc,
        )

    combined_symbols = set(liquid_symbols) | newly_admitted_symbols
    if not combined_symbols:
        fallback_symbols = exchange_symbols_normalized[:top_n]
        logger.info(
            '??????-????????: ??? ??????????? ???? -> fallback ?? ?????????? top_n=%s',
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
        '??????-????????: ?????=cache+exchange-liquidity, ????? ?? ?????=%s -> ? ???? ? ???????=%s -> ??? ?????? ?????? ???????????=%s -> ???????? ???????????? ???=%s -> ????? top_n=%s',
        len(exchange_symbols_normalized),
        len(symbols_with_volume),
        len(liquid_symbols),
        len(combined_symbols),
        len(ranked_top_symbols),
    )
    logger.info(
        '??????-????????: ?????? ??????????? ?? ?????????????? ?????? (???_avg_daily_volume_usd=%.2f) ?????????=%s',
        min_volume_usd,
        len(symbols_with_volume) - len(liquid_symbols),
    )
    logger.info(
        '??????-????????: newly admitted symbols ?? ???????? ??????=%s',
        len(newly_admitted_symbols),
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

    futures_symbol_map = {
        normalize_symbol(symbol): symbol
        for symbol in futures_symbols_raw
    }
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
        logger.warning("explicit-symbols: skipped_missing=%s", ",".join(missing))
    logger.info("explicit-symbols: requested=%s resolved=%s", len(requested_symbols), len(resolved))
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


def _resolve_review_timeframe(args: argparse.Namespace) -> Timeframe:
    return _resolve_timeframe(
        getattr(args, "tf", None),
        fallback=Timeframe.M15,
        argument_name="--tf",
    )


def _resolve_review_timeframes(args: argparse.Namespace) -> list[Timeframe]:
    if bool(getattr(args, "tf_all", False)):
        return [Timeframe.M15, Timeframe.M5]
    return [_resolve_review_timeframe(args)]


def _with_review_timeframe(args: argparse.Namespace, timeframe: Timeframe, *, output_path: Path | None = None) -> argparse.Namespace:
    cloned = argparse.Namespace(**vars(args))
    cloned.tf = timeframe.value
    cloned.tf_all = False
    if output_path is not None:
        cloned.output = str(output_path)
    return cloned


def _append_timeframe_suffix(path: Path, timeframe: Timeframe) -> Path:
    return path.with_name(f"{path.stem}_{timeframe.value}{path.suffix}")


def _resolve_plot_scope(args: argparse.Namespace, *, default_scope: str = "latest") -> str:
    raw_value = getattr(args, "plot_scope", None)
    if raw_value is None:
        return "all" if default_scope == "all" else "latest"
    return "all" if str(raw_value).strip().lower() == "all" else "latest"


def _resolve_stage3_review_mode(args: argparse.Namespace) -> str:
    return "evolution" if str(getattr(args, "review_mode", "snapshot")).strip().lower() == "evolution" else "snapshot"


def _resolve_stage4_param_grid() -> tuple[BeeBiteStage4PostmortemParams, ...]:
    return tuple(get_bee_bite_stage4_postmortem_grid())


def _select_plot_payload(items: list[_TItem], *, plot_scope: str, plot_limit: int) -> list[_TItem]:
    if not items:
        return []
    if plot_scope != "all":
        return items[:1]
    return items[: max(1, plot_limit)]


def _select_failed_plot_payload(items: list[_TItem], *, plot_scope: str, plot_limit: int) -> list[_TItem]:
    if not items:
        return []
    if plot_scope == "latest":
        return items[:1]
    return items[: max(1, plot_limit)]


def _slugify_reason(value: str) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "_" for character in value.strip())
    compact = "_".join(part for part in normalized.split("_") if part)
    return compact or "unknown_reason"


def _resolve_snapshot_timestamps(
    *,
    frame: pd.DataFrame,
    start_timestamp: int,
    end_timestamp: int,
) -> list[int]:
    prepared = frame.loc[:, ["timestamp"]].copy()
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp"])
    prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    if prepared.empty:
        return []

    timestamps = prepared["timestamp"].astype("int64").to_numpy()
    start_matches = np.where(timestamps == int(start_timestamp))[0]
    if start_matches.size == 0:
        return []
    end_matches = np.where(timestamps == int(end_timestamp))[0]
    if end_matches.size == 0:
        return []

    start_idx = int(start_matches[0])
    end_idx = int(end_matches[-1])
    if end_idx < start_idx:
        return []
    return [int(timestamp) for timestamp in timestamps[start_idx : end_idx + 1]]


def _stage2_has_reference_box(stage2_result: BeeBiteStage2Result) -> bool:
    if not stage2_result.passed:
        return False
    if stage2_result.box_low is None or stage2_result.box_high is None or stage2_result.box_end_timestamp is None:
        return False
    return any(zone.side == "lower" for zone in stage2_result.liquidity_zones)


def _stage23_can_lock_reference_box(
    *,
    snapshot_timestamp: int,
    stage1_event: BeeBiteStage1Result,
    stage2_result: BeeBiteStage2Result,
) -> bool:
    if not _stage2_has_reference_box(stage2_result):
        return False
    confirmed_timestamp = stage1_event.stage1_confirmed_timestamp
    if confirmed_timestamp is None:
        return False
    if int(snapshot_timestamp) < int(confirmed_timestamp):
        return False
    if stage2_result.box_start_timestamp is None or stage2_result.box_end_timestamp is None:
        return False

    lower_zones = [zone for zone in stage2_result.liquidity_zones if zone.side == "lower"]
    if not lower_zones:
        return False
    box_low = float(stage2_result.box_low or 0.0)
    box_high = float(stage2_result.box_high or box_low)
    range_height = max(box_high - box_low, 1e-12)
    boundary_tolerance = max(range_height * 0.15, 1e-12)

    def _zone_distance_to_boundary(zone: BeeBiteStage2LiquidityZone) -> float:
        if zone.low <= box_low <= zone.high:
            return 0.0
        if zone.high < box_low:
            return box_low - zone.high
        return zone.low - box_low

    lower_zone = max(
        lower_zones,
        key=lambda zone: (
            zone.low <= (box_low + boundary_tolerance) and zone.high >= (box_low - boundary_tolerance),
            -_zone_distance_to_boundary(zone),
            zone.touch_count,
            zone.end_timestamp - zone.start_timestamp,
            zone.end_timestamp,
        ),
    )
    zone_duration_ms = max(int(lower_zone.end_timestamp - lower_zone.start_timestamp), 0)
    box_duration_ms = max(int(stage2_result.box_end_timestamp - stage2_result.box_start_timestamp), 0)
    if box_duration_ms <= 0:
        return False
    return zone_duration_ms >= int(math.ceil(box_duration_ms * 0.5))


def _resolve_stage3_lower_zone_end_timestamp(stage3_result: BeeBiteStage3Result) -> int | None:
    lower_zone = stage3_result.active_lower_liquidity_zone
    if lower_zone is None:
        return None
    if stage3_result.break_timestamp is not None:
        return int(stage3_result.break_timestamp)
    if stage3_result.analysis_end_timestamp is not None:
        return int(stage3_result.analysis_end_timestamp)
    return int(lower_zone.end_timestamp)


def _build_reference_box_stale_result(
    *,
    symbol: str,
    stage2_result: BeeBiteStage2Result,
    snapshot_timestamp: int,
    reason: str,
) -> BeeBiteStage3Result:
    reference_box_start_timestamp = (
        int(stage2_result.box_start_timestamp) if stage2_result.box_start_timestamp is not None else None
    )
    reference_box_end_timestamp = (
        int(stage2_result.box_end_timestamp) if stage2_result.box_end_timestamp is not None else None
    )
    return BeeBiteStage3Result(
        symbol=symbol,
        passed=False,
        reason=reason,
        analysis_start_timestamp=reference_box_end_timestamp,
        analysis_end_timestamp=int(snapshot_timestamp),
        active_lower_liquidity_zone=None,
        box_low=float(stage2_result.box_low) if stage2_result.box_low is not None else None,
        box_high=float(stage2_result.box_high) if stage2_result.box_high is not None else None,
        reference_box_start_timestamp=reference_box_start_timestamp,
        reference_box_end_timestamp=reference_box_end_timestamp,
    )


def _is_stage3_terminal_failure(stage3_result: BeeBiteStage3Result) -> bool:
    if stage3_result.passed:
        return False
    return stage3_result.reason in {
        "break_too_deep",
        "close_below_hold",
        "sweep_missed_lower_zone",
        "under_range_span_too_wide",
    }


def _get_cached_stage2_result(
    *,
    cache: dict[int, BeeBiteStage2Result],
    symbol: str,
    frame: pd.DataFrame,
    stage1_event: BeeBiteStage1Result,
    stage2_detector: BeeBiteStage2Detector,
    analysis_end_timestamp: int,
) -> BeeBiteStage2Result:
    cached = cache.get(int(analysis_end_timestamp))
    if cached is not None:
        return cached
    result = stage2_detector.detect(
        symbol=symbol,
        frame=frame,
        stage1=stage1_event,
        analysis_end_timestamp=int(analysis_end_timestamp),
    )
    cache[int(analysis_end_timestamp)] = result
    return result


def _get_cached_review_frame(
    *,
    cache: dict[tuple[str, Timeframe], pd.DataFrame],
    preparer: DataPreparer,
    symbol: str,
    timeframe: Timeframe,
) -> pd.DataFrame:
    cache_key = (symbol, timeframe)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    loaded = preparer.load_symbol_data(symbol, timeframe)
    cache[cache_key] = loaded
    return loaded


def _get_cached_stage1_review(
    *,
    cache: dict[tuple[str, Timeframe], tuple[list[BeeBiteStage1Result], BeeBiteStage1Result | None]],
    selector: BeeBiteStage1Selector,
    symbol: str,
    timeframe: Timeframe,
    frame: pd.DataFrame,
) -> tuple[list[BeeBiteStage1Result], BeeBiteStage1Result | None]:
    cache_key = (symbol, timeframe)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    events = selector.detect_events(symbol=symbol, frame=frame)
    evaluation: BeeBiteStage1Result | None = None
    if not events:
        evaluation = selector.evaluate_symbol(symbol=symbol, frame=frame)
    cache[cache_key] = (events, evaluation)
    return events, evaluation


def _reset_review_plot_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def _build_stage23_evolution_snapshots(
    *,
    symbol: str,
    timeframe: Timeframe,
    frame: pd.DataFrame,
    stage1_event: BeeBiteStage1Result,
    regime: _Stage1Regime,
    stage2_detector: BeeBiteStage2Detector,
    stage3_detector: BeeBiteStage3Detector,
    initial_stage2_result: BeeBiteStage2Result | None = None,
) -> list[_Stage23EvolutionSnapshot]:
    if stage1_event.pump_peak_timestamp is None:
        return []

    required_columns = {"timestamp"}
    if frame.empty or not required_columns.issubset(frame.columns):
        return []
    prepared = frame.loc[:, ["timestamp"]].copy()
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp"])
    prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    if prepared.empty:
        return []
    analysis_end_timestamp = _resolve_stage23_analysis_end_timestamp(
        frame=frame,
        timeframe=timeframe,
        regime=regime,
    )

    snapshot_timestamps = _resolve_snapshot_timestamps(
        frame=frame,
        start_timestamp=int(stage1_event.pump_peak_timestamp) + timeframe.to_milliseconds(),
        end_timestamp=analysis_end_timestamp,
    )
    snapshots: list[_Stage23EvolutionSnapshot] = []
    reference_stage2_result: BeeBiteStage2Result | None = None
    stage2_results_by_timestamp: dict[int, BeeBiteStage2Result] = {}
    if initial_stage2_result is not None and initial_stage2_result.analysis_end_timestamp is not None:
        stage2_results_by_timestamp[int(initial_stage2_result.analysis_end_timestamp)] = initial_stage2_result

    for snapshot_order, snapshot_timestamp in enumerate(snapshot_timestamps, start=1):
        stale_stage3_result: BeeBiteStage3Result | None = None
        if reference_stage2_result is not None:
            stale_reason = stage3_detector.resolve_reference_box_staleness(
                frame=frame,
                stage1=stage1_event,
                stage2=reference_stage2_result,
                analysis_end_timestamp=int(snapshot_timestamp),
            )
            if stale_reason is not None:
                stale_stage3_result = _build_reference_box_stale_result(
                    symbol=symbol,
                    stage2_result=reference_stage2_result,
                    snapshot_timestamp=int(snapshot_timestamp),
                    reason=stale_reason,
                )
                reference_stage2_result = None

        dynamic_stage2_result = _get_cached_stage2_result(
            cache=stage2_results_by_timestamp,
            symbol=symbol,
            frame=frame,
            stage1_event=stage1_event,
            stage2_detector=stage2_detector,
            analysis_end_timestamp=int(snapshot_timestamp),
        )
        if reference_stage2_result is None and _stage23_can_lock_reference_box(
            snapshot_timestamp=snapshot_timestamp,
            stage1_event=stage1_event,
            stage2_result=dynamic_stage2_result,
        ):
            dynamic_stale_reason = stage3_detector.resolve_reference_box_staleness(
                frame=frame,
                stage1=stage1_event,
                stage2=dynamic_stage2_result,
                analysis_end_timestamp=int(snapshot_timestamp),
            )
            if dynamic_stale_reason is None:
                reference_stage2_result = dynamic_stage2_result
            else:
                stale_stage3_result = _build_reference_box_stale_result(
                    symbol=symbol,
                    stage2_result=dynamic_stage2_result,
                    snapshot_timestamp=int(snapshot_timestamp),
                    reason=dynamic_stale_reason,
                )

        if reference_stage2_result is None:
            stage3_result = stale_stage3_result or BeeBiteStage3Result(
                symbol=symbol,
                passed=False,
                reason="stage2_reference_not_locked",
                analysis_end_timestamp=snapshot_timestamp,
            )
        else:
            stage3_result = stage3_detector.detect(
                symbol=symbol,
                frame=frame,
                stage1=stage1_event,
                stage2=reference_stage2_result,
                analysis_end_timestamp=snapshot_timestamp,
            )

        snapshots.append(
            _Stage23EvolutionSnapshot(
                order=snapshot_order,
                timestamp=snapshot_timestamp,
                dynamic_stage2_result=dynamic_stage2_result,
                reference_stage2_result=reference_stage2_result,
                stage3_result=stage3_result,
            )
        )

    return snapshots


def _resolve_stage23_terminal_result(
    *,
    symbol: str,
    timeframe: Timeframe,
    frame: pd.DataFrame,
    stage1_event: BeeBiteStage1Result,
    regime: _Stage1Regime,
    stage2_detector: BeeBiteStage2Detector,
    stage3_detector: BeeBiteStage3Detector,
    initial_stage2_result: BeeBiteStage2Result | None = None,
) -> BeeBiteStage3Result:
    if stage1_event.pump_peak_timestamp is None:
        return BeeBiteStage3Result(symbol=symbol, passed=False, reason="stage1_peak_missing")

    required_columns = {"timestamp"}
    if frame.empty or not required_columns.issubset(frame.columns):
        return BeeBiteStage3Result(symbol=symbol, passed=False, reason="frame_invalid")

    prepared = frame.loc[:, ["timestamp"]].copy()
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp"])
    prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    if prepared.empty:
        return BeeBiteStage3Result(symbol=symbol, passed=False, reason="frame_invalid")

    analysis_end_timestamp = _resolve_stage23_analysis_end_timestamp(
        frame=frame,
        timeframe=timeframe,
        regime=regime,
    )
    snapshot_timestamps = _resolve_snapshot_timestamps(
        frame=frame,
        start_timestamp=int(stage1_event.pump_peak_timestamp) + timeframe.to_milliseconds(),
        end_timestamp=analysis_end_timestamp,
    )
    if not snapshot_timestamps:
        return BeeBiteStage3Result(symbol=symbol, passed=False, reason="stage2_reference_not_locked")

    reference_stage2_result: BeeBiteStage2Result | None = None
    last_actionable_stage3_result: BeeBiteStage3Result | None = None
    last_reference_locked_stage3_result: BeeBiteStage3Result | None = None
    stage2_results_by_timestamp: dict[int, BeeBiteStage2Result] = {}
    if initial_stage2_result is not None and initial_stage2_result.analysis_end_timestamp is not None:
        stage2_results_by_timestamp[int(initial_stage2_result.analysis_end_timestamp)] = initial_stage2_result

    for snapshot_timestamp in snapshot_timestamps:
        stale_stage3_result: BeeBiteStage3Result | None = None
        if reference_stage2_result is not None:
            stale_reason = stage3_detector.resolve_reference_box_staleness(
                frame=frame,
                stage1=stage1_event,
                stage2=reference_stage2_result,
                analysis_end_timestamp=int(snapshot_timestamp),
            )
            if stale_reason is not None:
                stale_stage3_result = _build_reference_box_stale_result(
                    symbol=symbol,
                    stage2_result=reference_stage2_result,
                    snapshot_timestamp=int(snapshot_timestamp),
                    reason=stale_reason,
                )
                last_actionable_stage3_result = stale_stage3_result
                reference_stage2_result = None

        if reference_stage2_result is None:
            dynamic_stage2_result = _get_cached_stage2_result(
                cache=stage2_results_by_timestamp,
                symbol=symbol,
                frame=frame,
                stage1_event=stage1_event,
                stage2_detector=stage2_detector,
                analysis_end_timestamp=int(snapshot_timestamp),
            )
            if _stage23_can_lock_reference_box(
                snapshot_timestamp=snapshot_timestamp,
                stage1_event=stage1_event,
                stage2_result=dynamic_stage2_result,
            ):
                dynamic_stale_reason = stage3_detector.resolve_reference_box_staleness(
                    frame=frame,
                    stage1=stage1_event,
                    stage2=dynamic_stage2_result,
                    analysis_end_timestamp=int(snapshot_timestamp),
                )
                if dynamic_stale_reason is None:
                    reference_stage2_result = dynamic_stage2_result
                else:
                    stale_stage3_result = _build_reference_box_stale_result(
                        symbol=symbol,
                        stage2_result=dynamic_stage2_result,
                        snapshot_timestamp=int(snapshot_timestamp),
                        reason=dynamic_stale_reason,
                    )
                    last_actionable_stage3_result = stale_stage3_result
            else:
                continue
        if reference_stage2_result is None:
            continue

        stage3_result = stage3_detector.detect(
            symbol=symbol,
            frame=frame,
            stage1=stage1_event,
            stage2=reference_stage2_result,
            analysis_end_timestamp=snapshot_timestamp,
        )
        last_reference_locked_stage3_result = stage3_result
        if (
            stage3_result.break_timestamp is not None
            or stage3_result.reclaim_timestamp is not None
            or stage3_result.invalidation_timestamp is not None
        ):
            last_actionable_stage3_result = stage3_result
        if stage3_result.passed:
            return stage3_result
        if _is_stage3_terminal_failure(stage3_result):
            return stage3_result

    if last_actionable_stage3_result is not None:
        return last_actionable_stage3_result
    if last_reference_locked_stage3_result is not None:
        return last_reference_locked_stage3_result
    return BeeBiteStage3Result(symbol=symbol, passed=False, reason="stage2_reference_not_locked")


def _select_terminal_stage23_snapshot(snapshots: list[_Stage23EvolutionSnapshot]) -> _Stage23EvolutionSnapshot | None:
    if not snapshots:
        return None

    passed_snapshots = [snapshot for snapshot in snapshots if snapshot.stage3_result.passed]
    if passed_snapshots:
        return passed_snapshots[0]

    actionable_failures = [
        snapshot
        for snapshot in snapshots
        if snapshot.stage3_result.break_timestamp is not None
        or snapshot.stage3_result.reclaim_timestamp is not None
        or snapshot.stage3_result.invalidation_timestamp is not None
    ]
    if actionable_failures:
        return actionable_failures[-1]

    reference_locked = [
        snapshot
        for snapshot in snapshots
        if snapshot.reference_stage2_result is not None
    ]
    if reference_locked:
        return reference_locked[-1]

    return snapshots[-1]


def _fetch_data_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("fetch-data", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)

    if args.top_n is not None and args.top_n <= 0:
        logger.error("fetch-data: --top-n must be > 0")
        return 1
    if args.days <= 0:
        logger.error("fetch-data: --days must be > 0")
        return 1

    fetcher, exchange_client = _build_fetch_stack(config)
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
    logger.info(
        "загрузка-данных: найдено фьючерсов=%s выбрано_символов=%s (режим_подбора=%s)",
        all_futures_count,
        len(symbols),
        "cache+exchange-liquidity",
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
            logger.info(
                "fetch-data: recomputed liquid symbol list after primary timeframe load (symbols=%s)",
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

    fetcher, exchange_client = _build_fetch_stack(config)
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
        stage1_selector = BeeBiteStage1Selector.for_timeframe(entry_timeframe)
        stage1_results: list[BeeBiteStage1Result] = []
        stage1_reason_counts: Counter[str] = Counter()
        for symbol in symbols:
            entry_frame = preparer.load_symbol_data(symbol, entry_timeframe)
            preloaded_entry_frames[symbol] = entry_frame
            if levels_timeframe == entry_timeframe:
                preloaded_levels_frames[symbol] = entry_frame

            stage1_result = stage1_selector.evaluate_symbol(symbol=symbol, frame=entry_frame)
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


def _stage1_event_to_row(
    event: BeeBiteStage1Result,
    *,
    timeframe: Timeframe | None = None,
    regime_index: int | None = None,
    regime_role: str | None = None,
    regime_end_timestamp: int | None = None,
    regime_end_reason: str | None = None,
    display_metrics: dict[str, object] | None = None,
) -> dict[str, object]:
    row = {
        "symbol": event.symbol,
        "timeframe": timeframe.value if timeframe is not None else None,
        "regime_index": regime_index,
        "regime_role": regime_role,
        "regime_end_timestamp": regime_end_timestamp,
        "regime_end_reason": regime_end_reason,
        "reason": event.reason,
        "sleep_start_timestamp": event.sleep_start_timestamp,
        "sleep_end_timestamp": event.sleep_end_timestamp,
        "pump_start_timestamp": event.pump_start_timestamp,
        "pump_peak_timestamp": event.pump_peak_timestamp,
        "stage1_confirmed_timestamp": event.stage1_confirmed_timestamp,
        "pump_base_price": event.pump_base_price,
        "pump_peak_price": event.pump_peak_price,
        "hold_base_price": event.hold_base_price,
        "hold_base_timestamp": event.hold_base_timestamp,
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
    if display_metrics:
        row.update(display_metrics)
    return row


def _resolve_stage1_display_metrics(
    *,
    frame: pd.DataFrame,
    event: BeeBiteStage1Result,
    window_end_timestamp: int | None,
) -> dict[str, object]:
    required_columns = {"timestamp", "open", "high", "low", "close", "volume"}
    if frame.empty or not required_columns.issubset(frame.columns):
        return {}

    prepared = frame.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]].copy()
    for column in prepared.columns:
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    if prepared.empty:
        return {}

    try:
        explicit_window_end_idx = BeeBiteStage1Plotter._timestamp_to_index(
            prepared,
            window_end_timestamp if window_end_timestamp is not None else event.stage1_confirmed_timestamp,
        )
        display_start_idx = BeeBiteStage1Plotter._timestamp_to_index(prepared, event.pump_start_timestamp)
        display_pump_base_price = float(event.pump_base_price or prepared.iloc[display_start_idx]["low"])
        display_peak_idx, display_peak_price = BeeBiteStage1Plotter._resolve_display_peak(
            prepared=prepared,
            pump_peak_timestamp=event.pump_peak_timestamp,
            fallback_peak_price=event.pump_peak_price,
            window_end_idx=explicit_window_end_idx,
        )
    except ValueError:
        return {}

    lowest_after_pump_idx, lowest_after_pump = BeeBiteStage1Plotter._resolve_display_low_after_peak(
        prepared=prepared,
        peak_idx=display_peak_idx,
        window_end_idx=explicit_window_end_idx,
    )
    hold_base_price = float(event.hold_base_price if event.hold_base_price is not None else (event.pump_base_price or 0.0))
    display_hold_price = (
        hold_base_price + ((display_peak_price - hold_base_price) * 0.5)
        if display_peak_price is not None and hold_base_price > 0.0
        else event.hold_price
    )
    pump_base_price = float(display_pump_base_price or 0.0)
    display_pump_percent = (
        (display_peak_price / pump_base_price) - 1.0
        if display_peak_price is not None and pump_base_price > 0.0
        else event.pump_percent
    )
    display_retain_ratio = None
    has_display_retrace_reference = (
        lowest_after_pump is not None
        and display_peak_price is not None
        and hold_base_price > 0.0
        and display_peak_price > hold_base_price
    )
    if has_display_retrace_reference:
        display_retain_ratio = (lowest_after_pump - hold_base_price) / max(display_peak_price - hold_base_price, 1e-12)

    display_pump_start_timestamp = int(prepared.iloc[display_start_idx]["timestamp"])
    display_peak_timestamp = int(prepared.iloc[display_peak_idx]["timestamp"])
    display_lowest_timestamp = int(prepared.iloc[lowest_after_pump_idx]["timestamp"]) if lowest_after_pump_idx is not None else None
    return {
        "display_pump_start_timestamp": display_pump_start_timestamp,
        "display_pump_base_price": display_pump_base_price,
        "display_pump_peak_timestamp": display_peak_timestamp,
        "display_pump_peak_price": display_peak_price,
        "display_hold_price": display_hold_price,
        "display_lowest_after_pump": lowest_after_pump,
        "display_lowest_after_pump_timestamp": display_lowest_timestamp,
        "display_pump_percent": display_pump_percent,
        "display_retain_ratio": display_retain_ratio,
    }


def _review_stage1_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("review-stage1", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    logger.info("review-stage1: cache_dir=%s", config.backtest.cache_dir)
    review_timeframe = _resolve_review_timeframe(args)
    results_dir = _resolve_results_dir_for_strategy(config.backtest.results_dir, "bee_bite")
    output_dir = results_dir / "stage1_review" / review_timeframe.value
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = Path(args.output) if getattr(args, "output", None) else output_dir / DEFAULT_STAGE1_EVENTS_OUTPUT_FILE
    plots_dir = output_dir / DEFAULT_STAGE1_PLOTS_DIR_NAME
    _reset_review_plot_dir(plots_dir)
    plot_limit = int(getattr(args, "plot_limit", 20) or 20)

    preparer = DataPreparer(config.backtest.cache_dir)
    symbols_raw = args.symbols or preparer.list_symbols(review_timeframe)
    symbols = [normalize_symbol(symbol) for symbol in symbols_raw]
    if not symbols:
        logger.info("review-stage1: нет данных в кэше для entry_tf=15m")
        return 0

    selector = BeeBiteStage1Selector.for_timeframe(review_timeframe)
    plotter = BeeBiteStage1Plotter()
    all_events: list[BeeBiteStage1Result] = []
    regimes_by_symbol: dict[str, list[_Stage1Regime]] = {}
    frames_by_symbol: dict[str, pd.DataFrame] = {}
    reason_counts: Counter[str] = Counter()
    reason_samples: dict[str, list[str]] = {}
    empty_frame_symbols: list[str] = []
    populated_frame_symbols = 0
    min_rows: int | None = None
    max_rows: int | None = None

    for index, symbol in enumerate(symbols, start=1):
        frame = preparer.load_symbol_data(symbol, review_timeframe)
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
            regimes_by_symbol[symbol] = _resolve_stage1_regime_ends(
                frame=frame,
                regimes=_group_stage1_events_into_regimes(events),
            )
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
                len(regimes_by_symbol),
                len(all_events),
                populated_frame_symbols,
                len(empty_frame_symbols),
            )

    rows: list[dict[str, object]] = []
    regimes_total = 0
    for symbol, regimes in regimes_by_symbol.items():
        frame = frames_by_symbol.get(symbol, pd.DataFrame())
        for regime in regimes:
            regimes_total += 1
            for event in regime.events:
                regime_role: str | None = None
                if event is regime.first_event and event is regime.last_event:
                    regime_role = "first_last"
                elif event is regime.first_event:
                    regime_role = "first"
                elif event is regime.last_event:
                    regime_role = "last"
                window_end_timestamp = event.stage1_confirmed_timestamp
                display_metrics = _resolve_stage1_display_metrics(
                    frame=frame,
                    event=event,
                    window_end_timestamp=window_end_timestamp,
                )
                rows.append(
                    _stage1_event_to_row(
                        event,
                        timeframe=review_timeframe,
                        regime_index=regime.regime_index,
                        regime_role=regime_role,
                        regime_end_timestamp=regime.regime_end_timestamp,
                        regime_end_reason=regime.regime_end_reason,
                        display_metrics=display_metrics,
                    )
                )
    events_frame = pd.DataFrame(rows)
    if not events_frame.empty:
        events_frame = events_frame.sort_values(
            ["symbol", "regime_index", "stage1_confirmed_timestamp", "pump_peak_timestamp"],
            ascending=[True, True, True, True],
        ).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    events_frame.to_csv(output_path, index=False)

    regimes_for_plots = sorted(
        [regime for regimes in regimes_by_symbol.values() for regime in regimes],
        key=lambda item: (
            int(item.last_event.stage1_confirmed_timestamp or 0),
            int(item.last_event.pump_peak_timestamp or 0),
            item.symbol,
            item.regime_index,
        ),
        reverse=True,
    )
    plots_built = 0
    for regime in regimes_for_plots[:plot_limit]:
        frame = frames_by_symbol.get(regime.symbol)
        if frame is None or frame.empty:
            continue
        symbol_slug = regime.symbol.replace("/", "_")
        first_output = plots_dir / f"{symbol_slug}_stage1_regime_{regime.regime_index:02d}_first.png"
        plotter.plot_event(
            frame=frame,
            event=regime.first_event,
            output_path=first_output,
            window_end_timestamp=regime.first_event.stage1_confirmed_timestamp,
            title_suffix=f"{review_timeframe.value} | regime {regime.regime_index:02d} first",
        )
        plots_built += 1

        last_output = plots_dir / f"{symbol_slug}_stage1_regime_{regime.regime_index:02d}_last.png"
        plotter.plot_event(
            frame=frame,
            event=regime.last_event,
            output_path=last_output,
            window_end_timestamp=regime.last_event.stage1_confirmed_timestamp,
            title_suffix=f"{review_timeframe.value} | regime {regime.regime_index:02d} last ({regime.regime_end_reason})",
        )
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
        "review-stage1: symbols=%s symbols_with_events=%s regimes=%s events=%s csv=%s plots=%s plots_dir=%s",
        len(symbols),
        len(regimes_by_symbol),
        regimes_total,
        len(all_events),
        output_path,
        plots_built,
        plots_dir,
    )
    return 0


def _stage2_result_to_rows(
    *,
    symbol: str,
    timeframe: Timeframe,
    regime: _Stage1Regime,
    stage1_event: BeeBiteStage1Result,
    stage2_result: BeeBiteStage2Result,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {
            "symbol": symbol,
            "timeframe": timeframe.value,
            "regime_index": regime.regime_index,
            "row_type": "summary",
            "stage1_confirmed_timestamp": stage1_event.stage1_confirmed_timestamp,
            "regime_end_timestamp": regime.regime_end_timestamp,
            "regime_end_reason": regime.regime_end_reason,
            "stage2_passed": stage2_result.passed,
            "stage2_reason": stage2_result.reason,
            "analysis_start_timestamp": stage2_result.analysis_start_timestamp,
            "analysis_end_timestamp": stage2_result.analysis_end_timestamp,
            "confirmed_highs_count": len(stage2_result.confirmed_highs),
            "local_ranges_count": len(stage2_result.local_ranges),
            "merged_ranges_count": len(stage2_result.merged_ranges),
            "liquidity_zones_count": len(stage2_result.liquidity_zones),
            "box_start_timestamp": stage2_result.box_start_timestamp,
            "box_end_timestamp": stage2_result.box_end_timestamp,
            "box_high": stage2_result.box_high,
            "box_low": stage2_result.box_low,
        }
    ]

    for order, confirmed_high in enumerate(stage2_result.confirmed_highs, start=1):
        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "regime_index": regime.regime_index,
                "row_type": "confirmed_high",
                "row_order": order,
                "timestamp": confirmed_high.timestamp,
                "price": confirmed_high.price,
                "idx": confirmed_high.idx,
            }
        )

    for order, local_range in enumerate(stage2_result.local_ranges, start=1):
        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "regime_index": regime.regime_index,
                "row_type": "local_range",
                "row_order": order,
                "start_timestamp": local_range.start_timestamp,
                "end_timestamp": local_range.end_timestamp,
                "confirmed_high_timestamp": local_range.confirmed_high_timestamp,
                "confirmed_high_price": local_range.confirmed_high_price,
                "high": local_range.high,
                "low": local_range.low,
                "bars": local_range.bars,
            }
        )

    for order, merged_range in enumerate(stage2_result.merged_ranges, start=1):
        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "regime_index": regime.regime_index,
                "row_type": "merged_range",
                "row_order": order,
                "start_timestamp": merged_range.start_timestamp,
                "end_timestamp": merged_range.end_timestamp,
                "high": merged_range.high,
                "low": merged_range.low,
                "bars": merged_range.bars,
                "source_ranges": merged_range.source_ranges,
                "confirmed_high_timestamps": ",".join(str(item) for item in merged_range.confirmed_high_timestamps),
                "confirmed_high_prices": ",".join(f"{item:.10f}" for item in merged_range.confirmed_high_prices),
            }
        )

    for order, liquidity_zone in enumerate(stage2_result.liquidity_zones, start=1):
        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "regime_index": regime.regime_index,
                "row_type": "liquidity_zone",
                "row_order": order,
                "side": liquidity_zone.side,
                "start_timestamp": liquidity_zone.start_timestamp,
                "end_timestamp": liquidity_zone.end_timestamp,
                "last_touch_timestamp": liquidity_zone.last_touch_timestamp,
                "high": liquidity_zone.high,
                "low": liquidity_zone.low,
                "touch_count": liquidity_zone.touch_count,
                "bars": (liquidity_zone.end_idx - liquidity_zone.start_idx) + 1,
            }
        )

    return rows


def _stage3_result_to_rows(
    *,
    symbol: str,
    timeframe: Timeframe,
    regime: _Stage1Regime,
    stage1_event: BeeBiteStage1Result,
    stage2_result: BeeBiteStage2Result,
    stage3_result: BeeBiteStage3Result,
) -> list[dict[str, object]]:
    lower_zone = stage3_result.active_lower_liquidity_zone
    lower_zone_end_timestamp = _resolve_stage3_lower_zone_end_timestamp(stage3_result)
    rows: list[dict[str, object]] = [
        {
            "symbol": symbol,
            "timeframe": timeframe.value,
            "regime_index": regime.regime_index,
            "row_type": "summary",
            "stage1_confirmed_timestamp": stage1_event.stage1_confirmed_timestamp,
            "regime_end_timestamp": regime.regime_end_timestamp,
            "regime_end_reason": regime.regime_end_reason,
            "stage2_passed": stage2_result.passed,
            "stage2_reason": stage2_result.reason,
            "stage3_passed": stage3_result.passed,
            "stage3_reason": stage3_result.reason,
            "analysis_start_timestamp": stage3_result.analysis_start_timestamp,
            "analysis_end_timestamp": stage3_result.analysis_end_timestamp,
            "stage2_box_start_timestamp": stage2_result.box_start_timestamp,
            "stage2_box_end_timestamp": stage2_result.box_end_timestamp,
            "stage2_box_low": stage2_result.box_low,
            "stage2_box_high": stage2_result.box_high,
            "reference_box_start_timestamp": stage3_result.reference_box_start_timestamp,
            "reference_box_end_timestamp": stage3_result.reference_box_end_timestamp,
            "box_start_timestamp": stage3_result.reference_box_start_timestamp,
            "box_end_timestamp": stage3_result.reference_box_end_timestamp,
            "box_low": stage3_result.box_low,
            "box_high": stage3_result.box_high,
            "hold_price": stage3_result.hold_price,
            "break_timestamp": stage3_result.break_timestamp,
            "reclaim_timestamp": stage3_result.reclaim_timestamp,
            "invalidation_timestamp": stage3_result.invalidation_timestamp,
            "lowest_break_timestamp": stage3_result.lowest_break_timestamp,
            "lowest_break_price": stage3_result.lowest_break_price,
            "below_range_high_price": stage3_result.below_range_high_price,
            "under_range_span": stage3_result.under_range_span,
            "under_range_span_pct": stage3_result.under_range_span_pct,
            "range_size_pct": stage3_result.range_size_pct,
            "bars_under_range": stage3_result.bars_under_range,
            "active_lower_zone_start_timestamp": (lower_zone.start_timestamp if lower_zone is not None else None),
            "active_lower_zone_end_timestamp": lower_zone_end_timestamp,
            "active_lower_zone_low": (lower_zone.low if lower_zone is not None else None),
            "active_lower_zone_high": (lower_zone.high if lower_zone is not None else None),
            "active_lower_zone_touch_count": (lower_zone.touch_count if lower_zone is not None else None),
        }
    ]
    if lower_zone is not None:
        lower_zone_bars = None
        if lower_zone_end_timestamp is not None:
            timeframe_ms = timeframe.to_milliseconds()
            lower_zone_bars = max(
                int((int(lower_zone_end_timestamp) - int(lower_zone.start_timestamp)) // max(timeframe_ms, 1)) + 1,
                1,
            )
        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "regime_index": regime.regime_index,
                "row_type": "active_lower_liquidity_zone",
                "row_order": 1,
                "start_timestamp": lower_zone.start_timestamp,
                "end_timestamp": lower_zone_end_timestamp,
                "last_touch_timestamp": lower_zone.last_touch_timestamp,
                "low": lower_zone.low,
                "high": lower_zone.high,
                "touch_count": lower_zone.touch_count,
                "bars": lower_zone_bars,
            }
        )
    return rows


def _stage23_backtest_row(
    *,
    symbol: str,
    timeframe: Timeframe,
    regime: _Stage1Regime,
    stage1_event: BeeBiteStage1Result,
    snapshot_order: int,
    snapshot_timestamp: int,
    stage2_result: BeeBiteStage2Result,
    stage3_result: BeeBiteStage3Result,
) -> dict[str, object]:
    lower_zone = stage3_result.active_lower_liquidity_zone
    lower_zone_end_timestamp = _resolve_stage3_lower_zone_end_timestamp(stage3_result)
    return {
        "symbol": symbol,
        "timeframe": timeframe.value,
        "regime_index": regime.regime_index,
        "row_type": "evolution",
        "row_order": snapshot_order,
        "snapshot_timestamp": snapshot_timestamp,
        "stage1_confirmed_timestamp": stage1_event.stage1_confirmed_timestamp,
        "regime_end_timestamp": regime.regime_end_timestamp,
        "regime_end_reason": regime.regime_end_reason,
        "stage2_passed": stage2_result.passed,
        "stage2_reason": stage2_result.reason,
        "stage2_analysis_end_timestamp": stage2_result.analysis_end_timestamp,
        "confirmed_highs_count": len(stage2_result.confirmed_highs),
        "local_ranges_count": len(stage2_result.local_ranges),
        "merged_ranges_count": len(stage2_result.merged_ranges),
        "liquidity_zones_count": len(stage2_result.liquidity_zones),
        "stage2_box_start_timestamp": stage2_result.box_start_timestamp,
        "stage2_box_end_timestamp": stage2_result.box_end_timestamp,
        "stage2_box_low": stage2_result.box_low,
        "stage2_box_high": stage2_result.box_high,
        "reference_box_start_timestamp": stage3_result.reference_box_start_timestamp,
        "reference_box_end_timestamp": stage3_result.reference_box_end_timestamp,
        "box_start_timestamp": stage3_result.reference_box_start_timestamp,
        "box_end_timestamp": stage3_result.reference_box_end_timestamp,
        "box_low": stage3_result.box_low,
        "box_high": stage3_result.box_high,
        "stage3_passed": stage3_result.passed,
        "stage3_reason": stage3_result.reason,
        "stage3_analysis_end_timestamp": stage3_result.analysis_end_timestamp,
        "hold_price": stage3_result.hold_price,
        "break_timestamp": stage3_result.break_timestamp,
        "reclaim_timestamp": stage3_result.reclaim_timestamp,
        "invalidation_timestamp": stage3_result.invalidation_timestamp,
        "lowest_break_timestamp": stage3_result.lowest_break_timestamp,
        "lowest_break_price": stage3_result.lowest_break_price,
        "below_range_high_price": stage3_result.below_range_high_price,
        "under_range_span": stage3_result.under_range_span,
        "under_range_span_pct": stage3_result.under_range_span_pct,
        "range_size_pct": stage3_result.range_size_pct,
        "bars_under_range": stage3_result.bars_under_range,
        "active_lower_zone_start_timestamp": (lower_zone.start_timestamp if lower_zone is not None else None),
        "active_lower_zone_end_timestamp": lower_zone_end_timestamp,
        "active_lower_zone_last_touch_timestamp": (lower_zone.last_touch_timestamp if lower_zone is not None else None),
        "active_lower_zone_low": (lower_zone.low if lower_zone is not None else None),
        "active_lower_zone_high": (lower_zone.high if lower_zone is not None else None),
        "active_lower_zone_touch_count": (lower_zone.touch_count if lower_zone is not None else None),
    }


def _resolve_stage4_active_upper_zones(reference_stage2_result: BeeBiteStage2Result) -> list[object]:
    return [
        zone
        for zone in reference_stage2_result.liquidity_zones
        if zone.side == "upper"
        and reference_stage2_result.box_end_timestamp is not None
        and zone.end_timestamp >= int(reference_stage2_result.box_end_timestamp)
    ]


def _resolve_index_by_timestamp_from_frame(frame: pd.DataFrame, timestamp: int) -> int | None:
    matches = frame.index[frame["timestamp"].astype("int64") == int(timestamp)]
    if len(matches) == 0:
        return None
    return int(matches[-1])


def _resolve_stage4_trade_outcome(
    *,
    frame: pd.DataFrame,
    entry_idx: int,
    stop_price: float,
    entry_price: float,
    tp1_price: float,
    tp2_price: float,
    tp3_price: float,
    tp1_share: float,
    tp2_share: float,
    tp3_share: float,
) -> dict[str, object]:
    required_columns = {"high", "low", "close"}
    if frame.empty or not required_columns.issubset(frame.columns):
        return {
            "outcome": "frame_invalid",
            "exit_idx": None,
            "exit_price": None,
            "tp1_hit": False,
            "tp1_timestamp": None,
            "tp2_hit": False,
            "tp2_timestamp": None,
            "tp3_hit": False,
            "tp3_timestamp": None,
            "be_armed": False,
            "exit_stop_price": stop_price,
            "realized_rr": None,
            "realized_pnl_pct": None,
        }

    prepared = frame.loc[:, ["high", "low", "close"]].copy()
    for column in prepared.columns:
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["high", "low", "close"]).reset_index(drop=True)
    return _resolve_stage4_trade_outcome_prepared(
        prepared=prepared,
        entry_idx=entry_idx,
        stop_price=stop_price,
        entry_price=entry_price,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        tp3_price=tp3_price,
        tp1_share=tp1_share,
        tp2_share=tp2_share,
        tp3_share=tp3_share,
    )


def _resolve_stage4_trade_outcome_prepared(
    *,
    prepared: pd.DataFrame,
    entry_idx: int,
    stop_price: float,
    entry_price: float,
    tp1_price: float,
    tp2_price: float,
    tp3_price: float,
    tp1_share: float,
    tp2_share: float,
    tp3_share: float,
) -> dict[str, object]:
    required_columns = {"high", "low", "close"}
    if prepared.empty or not required_columns.issubset(prepared.columns):
        return {
            "outcome": "frame_invalid",
            "exit_idx": None,
            "exit_price": None,
            "tp1_hit": False,
            "tp1_timestamp": None,
            "tp2_hit": False,
            "tp2_timestamp": None,
            "tp3_hit": False,
            "tp3_timestamp": None,
            "be_armed": False,
            "exit_stop_price": stop_price,
            "realized_rr": None,
            "realized_pnl_pct": None,
        }

    risk = entry_price - stop_price
    remaining_share = 1.0
    active_stop = stop_price
    tp1_hit = False
    tp2_hit = False
    tp3_hit = False
    tp1_timestamp: int | None = None
    tp2_timestamp: int | None = None
    tp3_timestamp: int | None = None
    fills: list[tuple[int, float, float]] = []
    fee_rate = BEE_BITE_STAGE4_TAKER_FEE_RATE
    slippage_rate = BEE_BITE_STAGE4_SLIPPAGE_RATE

    def _record_fill(*, idx: int, price: float, share: float) -> None:
        nonlocal remaining_share
        if share <= 0.0 or remaining_share <= 0.0:
            return
        normalized_share = min(max(share, 0.0), remaining_share)
        fills.append((idx, price, normalized_share))
        remaining_share = max(0.0, remaining_share - normalized_share)

    def _finalize(*, outcome: str, exit_idx: int | None, exit_price: float | None) -> dict[str, object]:
        realized_rr = None
        gross_realized_rr = None
        total_fee_in_price = None
        total_fee_pct = None
        if risk > 0.0 and fills:
            gross_pnl_in_price = sum((share * (price - entry_price)) for _, price, share in fills)
            exit_fee_in_price = sum((share * price * fee_rate) for _, price, share in fills)
            entry_fee_in_price = entry_price * fee_rate
            total_fee_in_price = entry_fee_in_price + exit_fee_in_price
            net_pnl_in_price = gross_pnl_in_price - total_fee_in_price
            gross_realized_rr = gross_pnl_in_price / risk
            realized_rr = net_pnl_in_price / risk
        realized_pnl_pct = None
        gross_realized_pnl_pct = None
        if entry_price > 0.0 and fills:
            gross_pnl_pct = sum((share * (((price - entry_price) / entry_price) * 100.0)) for _, price, share in fills)
            exit_fee_pct = sum((share * ((price / entry_price) * fee_rate * 100.0)) for _, price, share in fills)
            entry_fee_pct = fee_rate * 100.0
            total_fee_pct = entry_fee_pct + exit_fee_pct
            gross_realized_pnl_pct = gross_pnl_pct
            realized_pnl_pct = gross_pnl_pct - total_fee_pct
        return {
            "outcome": outcome,
            "exit_idx": exit_idx,
            "exit_price": exit_price,
            "tp1_hit": tp1_hit,
            "tp1_timestamp": tp1_timestamp,
            "tp2_hit": tp2_hit,
            "tp2_timestamp": tp2_timestamp,
            "tp3_hit": tp3_hit,
            "tp3_timestamp": tp3_timestamp,
            "be_armed": tp1_hit and remaining_share > 0.0,
            "exit_stop_price": active_stop,
            "fee_rate": fee_rate,
            "slippage_rate": slippage_rate,
            "gross_realized_rr": gross_realized_rr,
            "realized_rr": realized_rr,
            "gross_realized_pnl_pct": gross_realized_pnl_pct,
            "realized_pnl_pct": realized_pnl_pct,
            "total_fee_in_price": total_fee_in_price,
            "total_fee_pct": total_fee_pct,
        }

    if entry_idx >= (len(prepared) - 1):
        final_idx = len(prepared) - 1
        final_close = float(prepared.iloc[final_idx]["close"])
        _record_fill(idx=final_idx, price=final_close, share=remaining_share)
        return _finalize(outcome="no_future_data", exit_idx=final_idx, exit_price=final_close)

    for idx in range(entry_idx + 1, len(prepared)):
        candle_high = float(prepared.iloc[idx]["high"])
        candle_low = float(prepared.iloc[idx]["low"])

        if not tp1_hit:
            hit_stop = candle_low <= active_stop
            hit_tp1 = candle_high >= tp1_price
            if hit_stop and hit_tp1:
                _record_fill(idx=idx, price=stop_price, share=remaining_share)
                return _finalize(outcome="stop_same_candle_pre_tp1", exit_idx=idx, exit_price=stop_price)
            if hit_stop:
                _record_fill(idx=idx, price=stop_price, share=remaining_share)
                return _finalize(outcome="stop_hit", exit_idx=idx, exit_price=stop_price)
            if hit_tp1:
                tp1_hit = True
                tp1_timestamp = idx
                _record_fill(idx=idx, price=tp1_price, share=tp1_share)
                active_stop = entry_price
                if remaining_share <= 0.0:
                    return _finalize(outcome="tp1_full_exit", exit_idx=idx, exit_price=tp1_price)
                if candle_low <= active_stop:
                    _record_fill(idx=idx, price=active_stop, share=remaining_share)
                    return _finalize(outcome="be_same_candle_after_tp1", exit_idx=idx, exit_price=active_stop)
                if not tp2_hit and tp2_share > 0.0 and candle_high >= tp2_price:
                    tp2_hit = True
                    tp2_timestamp = idx
                    _record_fill(idx=idx, price=tp2_price, share=tp2_share)
                    if remaining_share <= 0.0:
                        return _finalize(outcome="tp2_final_hit", exit_idx=idx, exit_price=tp2_price)
                if tp3_share > 0.0 and candle_high >= tp3_price:
                    tp3_hit = True
                    tp3_timestamp = idx
                    _record_fill(idx=idx, price=tp3_price, share=remaining_share)
                    return _finalize(outcome="tp3_hit", exit_idx=idx, exit_price=tp3_price)
                continue

        hit_stop = candle_low <= active_stop
        if hit_stop:
            _record_fill(idx=idx, price=active_stop, share=remaining_share)
            return _finalize(outcome="be_hit", exit_idx=idx, exit_price=active_stop)

        if not tp2_hit and tp2_share > 0.0 and candle_high >= tp2_price:
            tp2_hit = True
            tp2_timestamp = idx
            _record_fill(idx=idx, price=tp2_price, share=tp2_share)
            if remaining_share <= 0.0:
                return _finalize(outcome="tp2_final_hit", exit_idx=idx, exit_price=tp2_price)

        if tp3_share > 0.0 and candle_high >= tp3_price:
            tp3_hit = True
            tp3_timestamp = idx
            _record_fill(idx=idx, price=tp3_price, share=remaining_share)
            return _finalize(outcome="tp3_hit", exit_idx=idx, exit_price=tp3_price)

    final_idx = len(prepared) - 1
    final_close = float(prepared.iloc[final_idx]["close"])
    _record_fill(idx=final_idx, price=final_close, share=remaining_share)
    return _finalize(outcome="open", exit_idx=final_idx, exit_price=final_close)


def _build_stage4_postmortem_rows(
    *,
    symbol: str,
    timeframe: Timeframe,
    regime: _Stage1Regime,
    stage1_event: BeeBiteStage1Result,
    reference_stage2_result: BeeBiteStage2Result,
    stage3_result: BeeBiteStage3Result,
    frame: pd.DataFrame,
    param_grid: tuple[BeeBiteStage4PostmortemParams, ...],
) -> list[dict[str, object]]:
    if (
        not stage3_result.passed
        or stage3_result.reclaim_idx is None
        or stage3_result.lowest_break_price is None
        or stage1_event.pump_peak_price is None
        or stage3_result.box_high is None
        or stage3_result.box_low is None
    ):
        return []

    required_columns = {"timestamp", "close", "high", "low"}
    if frame.empty or not required_columns.issubset(frame.columns):
        return []
    prepared = frame.loc[:, ["timestamp", "close", "high", "low"]].copy()
    for column in prepared.columns:
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "close", "high", "low"]).reset_index(drop=True)
    if prepared.empty or stage3_result.reclaim_idx >= len(prepared):
        return []

    entry_idx = int(stage3_result.reclaim_idx)
    entry_timestamp = int(prepared.iloc[entry_idx]["timestamp"])
    entry_price = float(prepared.iloc[entry_idx]["close"])
    stop_price = float(stage3_result.lowest_break_price)
    peak_price = float(stage1_event.pump_peak_price)
    box_height = max(float(stage3_result.box_high) - float(stage3_result.box_low), 0.0)
    peak_idx = _resolve_index_by_timestamp_from_frame(prepared, int(stage1_event.pump_peak_timestamp or 0))
    if peak_idx is None:
        return []
    highest_high_after_peak = float(prepared.iloc[peak_idx : entry_idx + 1]["high"].max())
    active_upper_zones = _resolve_stage4_active_upper_zones(reference_stage2_result)
    upper_zone_above_peak = sorted(
        (zone for zone in active_upper_zones if zone.low > peak_price),
        key=lambda zone: zone.low,
    )
    risk = entry_price - stop_price
    trade_frame = prepared.loc[:, ["high", "low", "close"]].copy()
    trade_outcome_cache: dict[tuple[float, float, float, float], dict[str, object]] = {}

    rows: list[dict[str, object]] = []
    for row_order, params in enumerate(param_grid, start=1):
        tp1_price = peak_price
        if upper_zone_above_peak:
            tp2_source = "upper_zone_bottom"
            tp2_price = float(upper_zone_above_peak[0].low)
        else:
            tp2_source = "fallback_high_after_peak"
            tp2_price = max(peak_price, highest_high_after_peak)
        tp3_price = peak_price + (box_height * float(params.tp3_multiplier))
        weighted_target_price = (
            (tp1_price * float(params.tp1_share))
            + (tp2_price * float(params.tp2_share))
            + (tp3_price * float(params.tp3_share))
        )
        rr_value = ((weighted_target_price - entry_price) / risk) if risk > 0.0 else None
        target_stack_valid = (
            tp1_price > entry_price
            and (float(params.tp2_share) <= 0.0 or tp2_price >= tp1_price)
            and (float(params.tp3_share) <= 0.0 or tp3_price >= max(tp1_price, tp2_price if float(params.tp2_share) > 0.0 else tp1_price))
        )
        eligible = bool(target_stack_valid) and rr_value is not None and rr_value >= float(params.min_rr)
        outcome = "rr_below_threshold"
        exit_idx: int | None = None
        exit_price: float | None = None
        realized_rr: float | None = None
        realized_pnl_pct: float | None = None
        exit_timestamp: int | None = None
        tp1_timestamp: int | None = None
        tp2_timestamp: int | None = None
        tp3_timestamp: int | None = None
        tp1_hit = False
        tp2_hit = False
        tp3_hit = False
        be_armed = False
        exit_stop_price = stop_price
        if not target_stack_valid:
            outcome = "invalid_target_stack"
        param_cache_key = (
            float(params.tp3_multiplier),
            float(params.tp1_share),
            float(params.tp2_share),
            float(params.tp3_share),
        )
        trade_outcome: dict[str, object] | None = None
        if target_stack_valid and rr_value is not None:
            trade_outcome = trade_outcome_cache.get(param_cache_key)
            if trade_outcome is None:
                trade_outcome = _resolve_stage4_trade_outcome_prepared(
                    prepared=trade_frame,
                    entry_idx=entry_idx,
                    stop_price=stop_price,
                    entry_price=entry_price,
                    tp1_price=tp1_price,
                    tp2_price=tp2_price,
                    tp3_price=tp3_price,
                    tp1_share=float(params.tp1_share),
                    tp2_share=float(params.tp2_share),
                    tp3_share=float(params.tp3_share),
                )
                trade_outcome_cache[param_cache_key] = trade_outcome
        if eligible and trade_outcome is not None:
            outcome = str(trade_outcome["outcome"])
            exit_idx = cast(int | None, trade_outcome["exit_idx"])
            exit_price = cast(float | None, trade_outcome["exit_price"])
            tp1_hit = bool(trade_outcome["tp1_hit"])
            tp2_hit = bool(trade_outcome["tp2_hit"])
            tp3_hit = bool(trade_outcome["tp3_hit"])
            tp1_raw_idx = cast(int | None, trade_outcome["tp1_timestamp"])
            tp2_raw_idx = cast(int | None, trade_outcome["tp2_timestamp"])
            tp3_raw_idx = cast(int | None, trade_outcome["tp3_timestamp"])
            tp1_timestamp = int(prepared.iloc[tp1_raw_idx]["timestamp"]) if tp1_raw_idx is not None and tp1_raw_idx < len(prepared) else None
            tp2_timestamp = int(prepared.iloc[tp2_raw_idx]["timestamp"]) if tp2_raw_idx is not None and tp2_raw_idx < len(prepared) else None
            tp3_timestamp = int(prepared.iloc[tp3_raw_idx]["timestamp"]) if tp3_raw_idx is not None and tp3_raw_idx < len(prepared) else None
            be_armed = bool(trade_outcome["be_armed"])
            exit_stop_price = float(cast(float | int, trade_outcome["exit_stop_price"]))
            if exit_idx is not None and exit_idx < len(prepared):
                exit_timestamp = int(prepared.iloc[exit_idx]["timestamp"])
            realized_rr = cast(float | None, trade_outcome["realized_rr"])
            realized_pnl_pct = cast(float | None, trade_outcome["realized_pnl_pct"])
            gross_realized_rr = cast(float | None, trade_outcome["gross_realized_rr"])
            gross_realized_pnl_pct = cast(float | None, trade_outcome["gross_realized_pnl_pct"])
            total_fee_in_price = cast(float | None, trade_outcome["total_fee_in_price"])
            total_fee_pct = cast(float | None, trade_outcome["total_fee_pct"])
        else:
            gross_realized_rr = None
            gross_realized_pnl_pct = None
            total_fee_in_price = None
            total_fee_pct = None

        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "regime_index": regime.regime_index,
                "row_type": "trade",
                "row_order": row_order,
                "tp3_multiplier": float(params.tp3_multiplier),
                "tp1_share": float(params.tp1_share),
                "tp2_share": float(params.tp2_share),
                "tp3_share": float(params.tp3_share),
                "stage3_reclaim_timestamp": stage3_result.reclaim_timestamp,
                "entry_timestamp": entry_timestamp,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price,
                "tp3_price": tp3_price,
                "tp2_source": tp2_source,
                "highest_high_after_peak": highest_high_after_peak,
                "weighted_target_price": weighted_target_price,
                "box_height": box_height,
                "minimal_rr": float(params.min_rr),
                "rr": rr_value,
                "target_stack_valid": target_stack_valid,
                "eligible": eligible,
                "fee_model": "binance_futures_taker",
                "taker_fee_rate": BEE_BITE_STAGE4_TAKER_FEE_RATE,
                "slippage_rate": BEE_BITE_STAGE4_SLIPPAGE_RATE,
                "tp1_hit": tp1_hit,
                "tp1_timestamp": tp1_timestamp,
                "tp2_hit": tp2_hit,
                "tp2_timestamp": tp2_timestamp,
                "tp3_hit": tp3_hit,
                "tp3_timestamp": tp3_timestamp,
                "be_armed": be_armed,
                "outcome": outcome,
                "exit_timestamp": exit_timestamp,
                "exit_price": exit_price,
                "exit_stop_price": exit_stop_price,
                "gross_realized_rr": gross_realized_rr,
                "realized_rr": realized_rr,
                "gross_realized_pnl_pct": gross_realized_pnl_pct,
                "realized_pnl_pct": realized_pnl_pct,
                "total_fee_in_price": total_fee_in_price,
                "total_fee_pct": total_fee_pct,
            }
        )
    return rows


def _build_stage4_postmortem_summary_rows(
    *,
    timeframe: Timeframe,
    param_grid: tuple[BeeBiteStage4PostmortemParams, ...],
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    trade_rows = [row for row in rows if row.get("row_type") == "trade"]
    return _build_stage4_postmortem_summary_rows_from_trade_rows(
        timeframe=timeframe,
        param_grid=param_grid,
        trade_rows=trade_rows,
    )


def _build_stage4_postmortem_summary_rows_from_trade_rows(
    *,
    timeframe: Timeframe,
    param_grid: tuple[BeeBiteStage4PostmortemParams, ...],
    trade_rows: list[dict[str, object]],
    ) -> list[dict[str, object]]:
    if not trade_rows:
        return []

    summary_rows: list[dict[str, object]] = []
    stage3_candidates = len({(str(row["symbol"]), int(row["regime_index"])) for row in trade_rows})
    previous_total_pnl_pct = 0.0
    previous_win_rate = 0.0
    previous_total_realized_rr = 0.0
    for row_order, params in enumerate(param_grid, start=1):
        threshold_rows = [
            row
            for row in trade_rows
            if float(row["minimal_rr"]) == float(params.min_rr)
            and float(row["tp3_multiplier"]) == float(params.tp3_multiplier)
            and float(row["tp1_share"]) == float(params.tp1_share)
            and float(row["tp2_share"]) == float(params.tp2_share)
            and float(row["tp3_share"]) == float(params.tp3_share)
        ]
        eligible_rows = [row for row in threshold_rows if bool(row["eligible"])]
        tp1_full_exits = sum(1 for row in eligible_rows if row["outcome"] == "tp1_full_exit")
        tp2_final_hits = sum(1 for row in eligible_rows if row["outcome"] == "tp2_final_hit")
        tp3_hits = sum(1 for row in eligible_rows if row["outcome"] == "tp3_hit")
        stop_hits = sum(1 for row in eligible_rows if row["outcome"] in {"stop_hit", "stop_same_candle_pre_tp1"})
        be_hits = sum(1 for row in eligible_rows if row["outcome"] in {"be_hit", "be_same_candle_after_tp1"})
        open_trades = sum(1 for row in eligible_rows if row["outcome"] in {"open", "no_future_data"})
        invalid_target_stack = sum(1 for row in threshold_rows if row["outcome"] == "invalid_target_stack")
        realized_rr_values = [
            float(row["realized_rr"])
            for row in eligible_rows
            if row.get("realized_rr") is not None
        ]
        realized_pnl_pct_values = [
            float(row["realized_pnl_pct"])
            for row in eligible_rows
            if row.get("realized_pnl_pct") is not None
        ]
        fee_pct_values = [
            float(row["total_fee_pct"])
            for row in eligible_rows
            if row.get("total_fee_pct") is not None
        ]
        loss_rr_values = [value for value in realized_rr_values if value < 0.0]
        gain_rr_values = [value for value in realized_rr_values if value > 0.0]
        closed_rows = [row for row in eligible_rows if row["outcome"] not in {"open", "no_future_data"}]
        profitable_closed_trades = sum(
            1
            for row in closed_rows
            if row.get("realized_pnl_pct") is not None and float(row["realized_pnl_pct"]) > 0.0
        )
        win_rate = (profitable_closed_trades / len(closed_rows)) if closed_rows else 0.0
        total_realized_rr = sum(realized_rr_values) if realized_rr_values else 0.0
        total_pnl_pct = sum(realized_pnl_pct_values) if realized_pnl_pct_values else 0.0
        summary_rows.append(
            {
                "symbol": "__summary__",
                "timeframe": timeframe.value,
                "regime_index": 0,
                "row_type": "summary",
                "row_order": row_order,
                "minimal_rr": float(params.min_rr),
                "tp3_multiplier": float(params.tp3_multiplier),
                "tp1_share": float(params.tp1_share),
                "tp2_share": float(params.tp2_share),
                "tp3_share": float(params.tp3_share),
                "stage3_candidates": stage3_candidates,
                "eligible_trades": len(eligible_rows),
                "rr_filtered_out": sum(1 for row in threshold_rows if not bool(row["eligible"])),
                "invalid_target_stack": invalid_target_stack,
                "tp1_full_exits": tp1_full_exits,
                "tp2_final_hits": tp2_final_hits,
                "tp3_hits": tp3_hits,
                "stop_hits": stop_hits,
                "be_hits": be_hits,
                "open_trades": open_trades,
                "closed_trades": len(closed_rows),
                "profitable_closed_trades": profitable_closed_trades,
                "win_rate": win_rate,
                "avg_realized_rr": (sum(realized_rr_values) / len(realized_rr_values)) if realized_rr_values else 0.0,
                "total_realized_rr": total_realized_rr,
                "avg_pnl_pct": (sum(realized_pnl_pct_values) / len(realized_pnl_pct_values)) if realized_pnl_pct_values else 0.0,
                "total_pnl_pct": total_pnl_pct,
                "avg_fee_pct": (sum(fee_pct_values) / len(fee_pct_values)) if fee_pct_values else 0.0,
                "total_fee_pct": sum(fee_pct_values) if fee_pct_values else 0.0,
                "profit_factor_rr": (sum(gain_rr_values) / abs(sum(loss_rr_values))) if loss_rr_values and abs(sum(loss_rr_values)) > 0.0 else (math.inf if gain_rr_values else 0.0),
                "delta_total_pnl_pct": total_pnl_pct - previous_total_pnl_pct,
                "delta_win_rate_pct": (win_rate - previous_win_rate) * 100.0,
                "delta_total_realized_rr": total_realized_rr - previous_total_realized_rr,
            }
        )
        previous_total_pnl_pct = total_pnl_pct
        previous_win_rate = win_rate
        previous_total_realized_rr = total_realized_rr
    return summary_rows


def _process_stage4_symbol(
    *,
    cache_dir: Path,
    symbol: str,
    review_timeframe: Timeframe,
    param_grid: tuple[BeeBiteStage4PostmortemParams, ...],
) -> dict[str, object]:
    preparer = DataPreparer(cache_dir)
    stage1_timeframe = Timeframe.M15 if review_timeframe != Timeframe.M15 else review_timeframe
    selector = BeeBiteStage1Selector.for_timeframe(stage1_timeframe)
    stage2_detector = BeeBiteStage2Detector()
    stage3_detector = BeeBiteStage3Detector()
    frame_cache: dict[tuple[str, Timeframe], pd.DataFrame] = {}
    stage2_timestamp_cache: dict[int, BeeBiteStage2Result] = {}

    frame = _get_cached_review_frame(
        cache=frame_cache,
        preparer=preparer,
        symbol=symbol,
        timeframe=review_timeframe,
    )
    if frame.empty:
        return {
            "symbol": symbol,
            "rows": [],
            "stage3_passed_count": 0,
            "reason_counts": {},
            "empty_frame": True,
        }

    stage1_frame = frame if stage1_timeframe == review_timeframe else _get_cached_review_frame(
        cache=frame_cache,
        preparer=preparer,
        symbol=symbol,
        timeframe=stage1_timeframe,
    )
    if stage1_frame.empty:
        return {
            "symbol": symbol,
            "rows": [],
            "stage3_passed_count": 0,
            "reason_counts": {"stage1:empty_reference_frame": 1},
            "empty_frame": False,
        }

    stage1_events = selector.detect_events(symbol=symbol, frame=stage1_frame)
    if not stage1_events:
        stage1_evaluation = selector.evaluate_symbol(symbol=symbol, frame=stage1_frame)
        reason = stage1_evaluation.reason if stage1_evaluation is not None else "unknown"
        return {
            "symbol": symbol,
            "rows": [],
            "stage3_passed_count": 0,
            "reason_counts": {f"stage1:{reason}": 1},
            "empty_frame": False,
        }

    regimes = _resolve_stage1_regime_ends(
        frame=stage1_frame,
        regimes=_group_stage1_events_into_regimes(stage1_events),
    )
    rows: list[dict[str, object]] = []
    reason_counts: Counter[str] = Counter()
    stage3_passed_count = 0
    for regime in regimes:
        stage1_event = regime.first_event
        dynamic_stage2_result = stage2_detector.detect(
            symbol=symbol,
            frame=frame,
            stage1=stage1_event,
            analysis_end_timestamp=int(regime.regime_end_timestamp),
        )
        if not dynamic_stage2_result.passed:
            reason_counts["stage2_not_passed"] += 1
            continue

        stage3_result = _resolve_stage23_terminal_result(
            symbol=symbol,
            timeframe=review_timeframe,
            frame=frame,
            stage1_event=stage1_event,
            regime=regime,
            stage2_detector=stage2_detector,
            stage3_detector=stage3_detector,
            initial_stage2_result=dynamic_stage2_result,
        )
        if not stage3_result.passed:
            reason_counts[stage3_result.reason] += 1
            continue

        stage3_passed_count += 1
        reference_stage2_end_timestamp = int(
            stage3_result.reference_box_end_timestamp
            or dynamic_stage2_result.analysis_end_timestamp
            or regime.regime_end_timestamp
        )
        reference_stage2_result = _get_cached_stage2_result(
            cache=stage2_timestamp_cache,
            symbol=symbol,
            frame=frame,
            stage1_event=stage1_event,
            stage2_detector=stage2_detector,
            analysis_end_timestamp=reference_stage2_end_timestamp,
        )
        rows.extend(
            _build_stage4_postmortem_rows(
                symbol=symbol,
                timeframe=review_timeframe,
                regime=regime,
                stage1_event=stage1_event,
                reference_stage2_result=reference_stage2_result,
                stage3_result=stage3_result,
                frame=frame,
                param_grid=param_grid,
            )
        )

    return {
        "symbol": symbol,
        "rows": rows,
        "stage3_passed_count": stage3_passed_count,
        "reason_counts": dict(reason_counts),
        "empty_frame": False,
    }


def _serialize_stage4_param_grid(
    param_grid: tuple[BeeBiteStage4PostmortemParams, ...],
) -> list[dict[str, float]]:
    return [
        {
            "min_rr": float(params.min_rr),
            "tp3_multiplier": float(params.tp3_multiplier),
            "tp1_share": float(params.tp1_share),
            "tp2_share": float(params.tp2_share),
            "tp3_share": float(params.tp3_share),
        }
        for params in param_grid
    ]


def _run_stage4_symbol_with_timeout(
    *,
    cache_dir: Path,
    symbol: str,
    review_timeframe: Timeframe,
    param_grid: tuple[BeeBiteStage4PostmortemParams, ...],
    timeout_seconds: int | None,
) -> tuple[dict[str, object] | None, str | None]:
    with tempfile.NamedTemporaryFile(prefix="stage4_symbol_", suffix=".json", delete=False) as handle:
        output_path = Path(handle.name)
    serialized_param_grid = json.dumps(_serialize_stage4_param_grid(param_grid))
    worker_code = (
        "import json, traceback\n"
        "from pathlib import Path\n"
        "from cli.commands import _process_stage4_symbol\n"
        "from strategy.bee_bite import BeeBiteStage4PostmortemParams\n"
        "from domain.enums.timeframe import Timeframe\n"
        f"output_path = Path(r'''{str(output_path)}''')\n"
        f"param_grid_payload = json.loads(r'''{serialized_param_grid}''')\n"
        "param_grid = tuple(BeeBiteStage4PostmortemParams(**item) for item in param_grid_payload)\n"
        "try:\n"
        f"    result = _process_stage4_symbol(cache_dir=Path(r'''{str(cache_dir)}'''), symbol=r'''{symbol}''', review_timeframe=Timeframe(r'''{review_timeframe.value}'''), param_grid=param_grid)\n"
        "    output_path.write_text(json.dumps({'ok': True, 'result': result}), encoding='utf-8')\n"
        "except Exception:\n"
        "    output_path.write_text(json.dumps({'ok': False, 'error': traceback.format_exc()}), encoding='utf-8')\n"
        "    raise\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", worker_code],
            cwd=str(Path.cwd()),
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        output_path.unlink(missing_ok=True)
        return None, "timeout"
    if not output_path.exists():
        return None, f"worker_exit_{completed.returncode}"
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    finally:
        output_path.unlink(missing_ok=True)
    if not bool(payload.get("ok")):
        return None, str(payload.get("error") or f"worker_exit_{completed.returncode}")
    return cast(dict[str, object], payload["result"]), None


def _run_stage4_plot_with_timeout(
    *,
    cache_dir: Path,
    symbol: str,
    review_timeframe: Timeframe,
    regime_index: int,
    reference_box_end_timestamp: int | None,
    trade_row: dict[str, object],
    output_path: Path,
    timeout_seconds: int | None,
) -> str | None:
    trade_row_payload = json.dumps(trade_row)
    worker_code = (
        "import json, traceback\n"
        "from pathlib import Path\n"
        "from cli.commands import _rebuild_stage4_plot_context\n"
        "from data.liquidity.bee_bite_stage4_plotter import BeeBiteStage4Plotter\n"
        "from domain.enums.timeframe import Timeframe\n"
        f"trade_row = json.loads(r'''{trade_row_payload}''')\n"
        "try:\n"
        f"    plot_context = _rebuild_stage4_plot_context(cache_dir=Path(r'''{str(cache_dir)}'''), symbol=r'''{symbol}''', review_timeframe=Timeframe(r'''{review_timeframe.value}'''), regime_index={regime_index}, reference_box_end_timestamp={repr(reference_box_end_timestamp)})\n"
        "    if plot_context is None:\n"
        "        raise RuntimeError('plot_context_rebuild_failed')\n"
        "    frame, stage1_event, reference_stage2_result, stage3_result = plot_context\n"
        "    BeeBiteStage4Plotter().plot_result(\n"
        "        frame=frame,\n"
        "        stage1_event=stage1_event,\n"
        "        reference_stage2_result=reference_stage2_result,\n"
        "        stage3_result=stage3_result,\n"
        "        trade_row=trade_row,\n"
        f"        output_path=Path(r'''{str(output_path)}'''),\n"
        "    )\n"
        "except Exception:\n"
        "    traceback.print_exc()\n"
        "    raise\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", worker_code],
            cwd=str(Path.cwd()),
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        return stderr or stdout or f"worker_exit_{completed.returncode}"
    return None


def _rebuild_stage4_plot_context(
    *,
    cache_dir: Path,
    symbol: str,
    review_timeframe: Timeframe,
    regime_index: int,
    reference_box_end_timestamp: int | None,
) -> tuple[pd.DataFrame, BeeBiteStage1Result, BeeBiteStage2Result, BeeBiteStage3Result] | None:
    preparer = DataPreparer(cache_dir)
    stage1_timeframe = Timeframe.M15 if review_timeframe != Timeframe.M15 else review_timeframe
    selector = BeeBiteStage1Selector.for_timeframe(stage1_timeframe)
    stage2_detector = BeeBiteStage2Detector()
    stage3_detector = BeeBiteStage3Detector()
    frame_cache: dict[tuple[str, Timeframe], pd.DataFrame] = {}
    stage2_timestamp_cache: dict[int, BeeBiteStage2Result] = {}

    frame = _get_cached_review_frame(
        cache=frame_cache,
        preparer=preparer,
        symbol=symbol,
        timeframe=review_timeframe,
    )
    if frame.empty:
        return None
    stage1_frame = frame if stage1_timeframe == review_timeframe else _get_cached_review_frame(
        cache=frame_cache,
        preparer=preparer,
        symbol=symbol,
        timeframe=stage1_timeframe,
    )
    if stage1_frame.empty:
        return None
    stage1_events = selector.detect_events(symbol=symbol, frame=stage1_frame)
    if not stage1_events:
        return None
    regimes = _resolve_stage1_regime_ends(
        frame=stage1_frame,
        regimes=_group_stage1_events_into_regimes(stage1_events),
    )
    target_regime = next((regime for regime in regimes if regime.regime_index == regime_index), None)
    if target_regime is None:
        return None
    stage1_event = target_regime.first_event
    dynamic_stage2_result = stage2_detector.detect(
        symbol=symbol,
        frame=frame,
        stage1=stage1_event,
        analysis_end_timestamp=int(target_regime.regime_end_timestamp),
    )
    if not dynamic_stage2_result.passed:
        return None
    stage3_result = _resolve_stage23_terminal_result(
        symbol=symbol,
        timeframe=review_timeframe,
        frame=frame,
        stage1_event=stage1_event,
        regime=target_regime,
        stage2_detector=stage2_detector,
        stage3_detector=stage3_detector,
        initial_stage2_result=dynamic_stage2_result,
    )
    if not stage3_result.passed:
        return None
    reference_stage2_result = _get_cached_stage2_result(
        cache=stage2_timestamp_cache,
        symbol=symbol,
        frame=frame,
        stage1_event=stage1_event,
        stage2_detector=stage2_detector,
        analysis_end_timestamp=int(
            reference_box_end_timestamp
            or stage3_result.reference_box_end_timestamp
            or dynamic_stage2_result.analysis_end_timestamp
            or target_regime.regime_end_timestamp
        ),
    )
    return frame, stage1_event, reference_stage2_result, stage3_result


def _format_stage4_metric(value: object, *, digits: int = 2, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    numeric = float(value)
    if math.isinf(numeric):
        return "inf"
    formatted = f"{numeric:.{digits}f}"
    return f"{formatted}%" if pct else formatted


def _format_stage4_win_rate(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100.0:.1f}%"


def _format_stage4_param_triplet(row: dict[str, object]) -> str:
    return (
        f"rr={float(row.get('minimal_rr', 0.0) or 0.0):.2f}, "
        f"m={float(row.get('tp3_multiplier', 0.0) or 0.0):.1f}, "
        f"w={float(row.get('tp1_share', 0.0) or 0.0):.2f}/"
        f"{float(row.get('tp2_share', 0.0) or 0.0):.2f}/"
        f"{float(row.get('tp3_share', 0.0) or 0.0):.2f}"
    )


def _build_markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    if not rows:
        return "_No rows._"
    lines = [
        f"| {' | '.join(headers)} |",
        f"| {' | '.join('---' for _ in headers)} |",
    ]
    for row in rows:
        lines.append(f"| {' | '.join(str(cell) for cell in row)} |")
    return "\n".join(lines)


def _resolve_stage4_float(row: dict[str, object], key: str) -> float:
    return float(row.get(key, 0.0) or 0.0)


def _build_stage4_stability_heatmap(summary_rows: list[dict[str, object]]) -> str:
    eligible_rows = [row for row in summary_rows if float(row.get("eligible_trades", 0.0) or 0.0) > 0.0]
    if not eligible_rows:
        return "_No eligible rows._"

    rr_values = sorted({float(row.get("minimal_rr", 0.0) or 0.0) for row in summary_rows})
    multiplier_values = sorted({float(row.get("tp3_multiplier", 0.0) or 0.0) for row in summary_rows})
    pair_map: dict[tuple[float, float], list[dict[str, object]]] = {}
    for row in summary_rows:
        key = (
            float(row.get("minimal_rr", 0.0) or 0.0),
            float(row.get("tp3_multiplier", 0.0) or 0.0),
        )
        pair_map.setdefault(key, []).append(row)

    headers = ["RR \\ M"] + [f"{value:.1f}" for value in multiplier_values]
    table_rows: list[list[object]] = []
    for rr_value in rr_values:
        row_cells: list[object] = [f"{rr_value:.2f}"]
        for multiplier in multiplier_values:
            pair_rows = pair_map.get((rr_value, multiplier), [])
            if not pair_rows:
                row_cells.append("n/a")
                continue
            eligible_pair_rows = [row for row in pair_rows if float(row.get("eligible_trades", 0.0) or 0.0) > 0.0]
            profiles_total = len(pair_rows)
            profiles_ok = len(eligible_pair_rows)
            if not eligible_pair_rows:
                row_cells.append(f"0/{profiles_total}")
                continue
            avg_score = sum(float(row.get("stage4_score", 0.0) or 0.0) for row in eligible_pair_rows) / len(eligible_pair_rows)
            best_score = max(float(row.get("stage4_score", 0.0) or 0.0) for row in eligible_pair_rows)
            row_cells.append(f"a{avg_score:.2f}/b{best_score:.2f}; {profiles_ok}/{profiles_total}")
        table_rows.append(row_cells)
    return _build_markdown_table(headers, table_rows)


def _collect_stage4_stability_pairs(summary_rows: list[dict[str, object]]) -> list[dict[str, float]]:
    if not summary_rows:
        return []
    pair_map: dict[tuple[float, float], list[dict[str, object]]] = {}
    for row in summary_rows:
        key = (
            float(row.get("minimal_rr", 0.0) or 0.0),
            float(row.get("tp3_multiplier", 0.0) or 0.0),
        )
        pair_map.setdefault(key, []).append(row)

    aggregate_rows: list[list[object]] = []
    for (rr_value, multiplier), pair_rows in pair_map.items():
        eligible_pair_rows = [row for row in pair_rows if float(row.get("eligible_trades", 0.0) or 0.0) > 0.0]
        profiles_total = len(pair_rows)
        profiles_ok = len(eligible_pair_rows)
        if not eligible_pair_rows:
            avg_score = 0.0
            best_score = 0.0
            avg_total_rr = 0.0
        else:
            avg_score = sum(float(row.get("stage4_score", 0.0) or 0.0) for row in eligible_pair_rows) / len(eligible_pair_rows)
            best_score = max(float(row.get("stage4_score", 0.0) or 0.0) for row in eligible_pair_rows)
            avg_total_rr = sum(float(row.get("total_realized_rr", 0.0) or 0.0) for row in eligible_pair_rows) / len(eligible_pair_rows)
        aggregate_rows.append(
            {
                "minimal_rr": rr_value,
                "tp3_multiplier": multiplier,
                "profiles_ok": float(profiles_ok),
                "profiles_total": float(profiles_total),
                "avg_score": avg_score,
                "best_score": best_score,
                "avg_total_rr": avg_total_rr,
            }
        )
    return sorted(
        aggregate_rows,
        key=lambda row: (row["avg_score"], row["profiles_ok"], row["best_score"], row["avg_total_rr"]),
        reverse=True,
    )


def _build_stage4_stability_pairs_table(summary_rows: list[dict[str, object]]) -> str:
    aggregate_rows = _collect_stage4_stability_pairs(summary_rows)
    if not aggregate_rows:
        return "_No eligible rows._"

    top_rows = aggregate_rows[:8]
    return _build_markdown_table(
        ["RR", "M", "Eligible Profiles", "All Profiles", "Avg Score", "Best Score", "Avg Total RR"],
        [
            [
                f"{row['minimal_rr']:.2f}",
                f"{row['tp3_multiplier']:.1f}",
                int(row["profiles_ok"]),
                int(row["profiles_total"]),
                f"{row['avg_score']:.3f}",
                f"{row['best_score']:.3f}",
                f"{row['avg_total_rr']:.2f}",
            ]
            for row in top_rows
        ],
    )


def _classify_stage4_report_verdict(
    *,
    months_analysed: int,
    monthly_match_count: int,
    avg_drift_rr: float,
    best_summary_row: dict[str, object],
) -> str:
    match_rate = (monthly_match_count / months_analysed) if months_analysed > 0 else 0.0
    profit_factor = _resolve_stage4_profit_factor_sort_value(best_summary_row.get("profit_factor_rr"))
    total_rr = _resolve_stage4_float(best_summary_row, "total_realized_rr")
    if months_analysed >= 4 and match_rate >= 0.6 and avg_drift_rr <= 0.75 and profit_factor >= 1.5 and total_rr > 0.0:
        return "Robust"
    if match_rate >= 0.4 and avg_drift_rr <= 1.5 and profit_factor >= 1.0 and total_rr > 0.0:
        return "Promising"
    if total_rr > 0.0:
        return "Positive but fragile"
    return "Unstable"


def _resolve_stage4_month_label(timestamp_ms: object) -> str | None:
    if timestamp_ms is None:
        return None
    return pd.to_datetime(int(timestamp_ms), unit="ms", utc=True).strftime("%Y-%m")


def _build_stage4_monthly_summary_map(
    *,
    timeframe: Timeframe,
    param_grid: tuple[BeeBiteStage4PostmortemParams, ...],
    trade_rows: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    monthly_trade_rows: dict[str, list[dict[str, object]]] = {}
    for row in trade_rows:
        month_label = _resolve_stage4_month_label(row.get("entry_timestamp"))
        if month_label is None:
            continue
        monthly_trade_rows.setdefault(month_label, []).append(row)

    monthly_summary_map: dict[str, list[dict[str, object]]] = {}
    for month_label, month_rows in sorted(monthly_trade_rows.items()):
        summary_rows = _build_stage4_postmortem_summary_rows_from_trade_rows(
            timeframe=timeframe,
            param_grid=param_grid,
            trade_rows=month_rows,
        )
        monthly_summary_map[month_label] = _score_stage4_summary_rows(summary_rows)
    return monthly_summary_map


def _render_stage4_postmortem_report(
    *,
    timeframe: Timeframe,
    symbols_count: int,
    stage3_passed_count: int,
    param_grid: tuple[BeeBiteStage4PostmortemParams, ...],
    summary_rows: list[dict[str, object]],
    trade_rows: list[dict[str, object]],
    reason_counts: Counter[str],
    timed_out_symbols: list[str],
) -> str:
    generated_at = pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    best_summary_row = _select_best_stage4_summary_row(summary_rows)
    eligible_year_rows = [row for row in summary_rows if float(row.get("eligible_trades", 0.0) or 0.0) > 0.0]
    leaderboard_rows = sorted(
        eligible_year_rows,
        key=lambda row: (
            float(row.get("stage4_score", 0.0) or 0.0),
            float(row.get("total_realized_rr", 0.0) or 0.0),
            _resolve_stage4_profit_factor_sort_value(row.get("profit_factor_rr")),
            float(row.get("total_pnl_pct", 0.0) or 0.0),
        ),
        reverse=True,
    )[:10]
    monthly_summary_map = _build_stage4_monthly_summary_map(
        timeframe=timeframe,
        param_grid=param_grid,
        trade_rows=trade_rows,
    )
    stability_pairs = _collect_stage4_stability_pairs(summary_rows)
    best_param_key = _resolve_stage4_param_key(best_summary_row) if best_summary_row is not None else None
    top_reasons = ", ".join(f"{reason}={count}" for reason, count in reason_counts.most_common(8)) if reason_counts else "none"
    lines: list[str] = [f"# Bee Bite Stage-4 Postmortem Report ({timeframe.value})", "", f"_Generated: {generated_at}_", ""]
    if reason_counts:
        lines.append(f"> Top rejection reasons before stage-4: `{top_reasons}`")
        lines.append("")

    lines.extend(["## Executive Summary", ""])
    if best_summary_row is None:
        lines.append("_No eligible stage-4 trades for this timeframe._")
        return "\n".join(lines).strip() + "\n"

    monthly_best_rows: list[list[object]] = []
    monthly_year_best_rows: list[list[object]] = []
    monthly_drift_rows: list[list[object]] = []
    monthly_match_count = 0
    drift_rr_values: list[float] = []
    drift_pnl_values: list[float] = []
    for month_label, month_summary_rows in monthly_summary_map.items():
        month_best = _select_best_stage4_summary_row(month_summary_rows)
        year_best_month_row = None
        if best_param_key is not None:
            for row in month_summary_rows:
                if _resolve_stage4_param_key(row) == best_param_key:
                    year_best_month_row = row
                    break
        if month_best is not None:
            monthly_best_rows.append(
                [
                    month_label,
                    _format_stage4_param_triplet(month_best),
                    f"{float(month_best.get('stage4_score', 0.0) or 0.0):.4f}",
                    int(month_best.get("eligible_trades", 0) or 0),
                    _format_stage4_win_rate(month_best.get("win_rate")),
                    _format_stage4_metric(month_best.get("total_realized_rr")),
                    _format_stage4_metric(month_best.get("profit_factor_rr")),
                    _format_stage4_metric(month_best.get("total_pnl_pct"), pct=True),
                ]
            )
        if month_best is not None and year_best_month_row is not None:
            matches_year_best = _resolve_stage4_param_key(month_best) == best_param_key
            if matches_year_best:
                monthly_match_count += 1
            delta_rr = _resolve_stage4_float(month_best, "total_realized_rr") - _resolve_stage4_float(year_best_month_row, "total_realized_rr")
            delta_pnl = _resolve_stage4_float(month_best, "total_pnl_pct") - _resolve_stage4_float(year_best_month_row, "total_pnl_pct")
            drift_rr_values.append(delta_rr)
            drift_pnl_values.append(delta_pnl)
            monthly_drift_rows.append(
                [
                    month_label,
                    "yes" if matches_year_best else "no",
                    _format_stage4_param_triplet(month_best),
                    int(month_best.get("eligible_trades", 0) or 0),
                    _format_stage4_metric(month_best.get("total_realized_rr")),
                    _format_stage4_metric(year_best_month_row.get("total_realized_rr")),
                    f"{delta_rr:+.2f}",
                    _format_stage4_metric(month_best.get("total_pnl_pct"), pct=True),
                    _format_stage4_metric(year_best_month_row.get("total_pnl_pct"), pct=True),
                    f"{delta_pnl:+.2f}%",
                ]
            )
        if year_best_month_row is not None:
            monthly_year_best_rows.append(
                [
                    month_label,
                    int(year_best_month_row.get("eligible_trades", 0) or 0),
                    _format_stage4_win_rate(year_best_month_row.get("win_rate")),
                    _format_stage4_metric(year_best_month_row.get("total_realized_rr")),
                    _format_stage4_metric(year_best_month_row.get("profit_factor_rr")),
                    _format_stage4_metric(year_best_month_row.get("total_pnl_pct"), pct=True),
                    int(year_best_month_row.get("tp3_hits", 0) or 0),
                    int(year_best_month_row.get("stop_hits", 0) or 0),
                    int(year_best_month_row.get("be_hits", 0) or 0),
                    int(year_best_month_row.get("open_trades", 0) or 0),
                ]
            )

    months_analysed = len(monthly_drift_rows)
    match_rate = (monthly_match_count / months_analysed) * 100.0 if months_analysed else 0.0
    avg_drift_rr = sum(drift_rr_values) / len(drift_rr_values) if drift_rr_values else 0.0
    avg_drift_pnl = sum(drift_pnl_values) / len(drift_pnl_values) if drift_pnl_values else 0.0
    verdict = _classify_stage4_report_verdict(
        months_analysed=months_analysed,
        monthly_match_count=monthly_match_count,
        avg_drift_rr=abs(avg_drift_rr),
        best_summary_row=best_summary_row,
    )
    best_pair = stability_pairs[0] if stability_pairs else None

    lines.append(
        _build_markdown_table(
            ["Metric", "Value"],
            [
                ["Verdict", verdict],
                ["Best full-period params", f"`{_format_stage4_param_triplet(best_summary_row)}`"],
                ["Score", f"{float(best_summary_row.get('stage4_score', 0.0) or 0.0):.4f}"],
                ["Eligible trades", int(best_summary_row.get("eligible_trades", 0) or 0)],
                ["Win rate", _format_stage4_win_rate(best_summary_row.get("win_rate"))],
                ["Total realized RR", _format_stage4_metric(best_summary_row.get("total_realized_rr"))],
                ["Profit factor RR", _format_stage4_metric(best_summary_row.get("profit_factor_rr"))],
                ["Total PnL (net)", _format_stage4_metric(best_summary_row.get("total_pnl_pct"), pct=True)],
                ["Average fee drag per trade", _format_stage4_metric(best_summary_row.get("avg_fee_pct"), pct=True)],
                ["Total fee drag", _format_stage4_metric(best_summary_row.get("total_fee_pct"), pct=True)],
                ["Fee model", f"Binance Futures taker {BEE_BITE_STAGE4_TAKER_FEE_RATE * 100.0:.2f}% in + out"],
                ["Slippage model", f"{BEE_BITE_STAGE4_SLIPPAGE_RATE * 100.0:.2f}%"],
                ["Months analysed", months_analysed],
                ["Monthly match rate", f"{match_rate:.1f}%"],
                ["Avg monthly dRR vs year-best", f"{avg_drift_rr:+.2f}"],
                ["Avg monthly dPnL vs year-best", f"{avg_drift_pnl:+.2f}%"],
                ["Timed out symbols", len(timed_out_symbols)],
            ],
        )
    )
    lines.extend(["", "## Key Takeaways", ""])
    lines.append(
        f"- The strongest full-period setup is `{_format_stage4_param_triplet(best_summary_row)}` with "
        f"`{_format_stage4_metric(best_summary_row.get('total_realized_rr'))}` net total RR and "
        f"`{_format_stage4_metric(best_summary_row.get('total_pnl_pct'), pct=True)}` net total PnL "
        f"after Binance taker fees."
    )
    lines.append(
        f"- Monthly drift is `{avg_drift_rr:+.2f}` RR on average and the yearly-best setup matches the monthly-best "
        f"configuration in `{monthly_match_count}/{months_analysed}` months."
        if months_analysed
        else "- Monthly drift is not available yet because there are no month-level comparable results."
    )
    if best_pair is not None:
        lines.append(
            f"- The most stable `RR x multiplier` slice is `rr={best_pair['minimal_rr']:.2f}, m={best_pair['tp3_multiplier']:.1f}` "
            f"with average score `{best_pair['avg_score']:.3f}` across "
            f"`{int(best_pair['profiles_ok'])}/{int(best_pair['profiles_total'])}` share profiles."
        )
    lines.append(
        f"- Outcome mix for the best full-period setup: "
        f"`tp1={int(best_summary_row.get('tp1_full_exits', 0) or 0)}`, "
        f"`tp2={int(best_summary_row.get('tp2_final_hits', 0) or 0)}`, "
        f"`tp3={int(best_summary_row.get('tp3_hits', 0) or 0)}`, "
        f"`stop={int(best_summary_row.get('stop_hits', 0) or 0)}`, "
        f"`be={int(best_summary_row.get('be_hits', 0) or 0)}`, "
        f"`open={int(best_summary_row.get('open_trades', 0) or 0)}`."
    )
    if timed_out_symbols:
        preview = ", ".join(timed_out_symbols[:10])
        suffix = " ..." if len(timed_out_symbols) > 10 else ""
        lines.append(f"- Timed out symbols skipped during this run: `{preview}{suffix}`.")

    lines.extend(["", "---", "", "## Full-Period Leaderboard", ""])

    lines.append(
        _build_markdown_table(
            ["Rank", "Params", "Score", "Trades", "WR", "Total RR (net)", "PF RR", "Total PnL (net)"],
            [
                [
                    rank,
                    _format_stage4_param_triplet(row),
                    f"{float(row.get('stage4_score', 0.0) or 0.0):.4f}",
                    int(row.get("eligible_trades", 0) or 0),
                    _format_stage4_win_rate(row.get("win_rate")),
                    _format_stage4_metric(row.get("total_realized_rr")),
                    _format_stage4_metric(row.get("profit_factor_rr")),
                    _format_stage4_metric(row.get("total_pnl_pct"), pct=True),
                ]
                for rank, row in enumerate(leaderboard_rows, start=1)
            ],
        )
    )

    lines.extend(["", "---", "", "## Parameter Stability", ""])
    lines.append("- Cell format: `aAVG/bBEST; eligible_profiles/all_profiles`.")
    lines.append("- Higher `aAVG` means the whole `RR x multiplier` slice is more stable across TP-share profiles.")
    lines.append("- Large gap between `aAVG` and `bBEST` usually means a narrow peak rather than robust behavior.")
    lines.append("")
    lines.append(_build_stage4_stability_heatmap(summary_rows))

    lines.extend(["", "### Most Stable RR x Multiplier Pairs", ""])
    lines.append(_build_stage4_stability_pairs_table(summary_rows))

    lines.extend(["", "---", "", "## Monthly Diagnostics", "", "### Best Params By Month", ""])
    lines.append(
        _build_markdown_table(
            ["Month", "Best Params", "Score", "Trades", "WR", "Total RR (net)", "PF RR", "Total PnL (net)"],
            monthly_best_rows,
        )
    )

    lines.extend(["", "### Monthly Results For Full-Period Best Params", ""])
    lines.append(f"Best params reused for each month: `{_format_stage4_param_triplet(best_summary_row)}`")
    lines.append("")
    lines.append(
        _build_markdown_table(
            ["Month", "Trades", "WR", "Total RR (net)", "PF RR", "Total PnL (net)", "TP3", "Stop", "BE", "Open"],
            monthly_year_best_rows,
        )
    )

    lines.extend(["", "### Best Yearly Vs Best Monthly Drift", ""])
    if monthly_drift_rows:
        lines.append(f"- Year-best params: `{_format_stage4_param_triplet(best_summary_row)}`")
        lines.append(f"- Months analysed: `{months_analysed}`")
        lines.append(f"- Months where monthly best == yearly best: `{monthly_match_count}/{months_analysed}` ({match_rate:.1f}%)")
        lines.append(f"- Average monthly RR drift vs yearly best: `{avg_drift_rr:+.2f}`")
        lines.append(f"- Average monthly PnL drift vs yearly best: `{avg_drift_pnl:+.2f}%`")
        lines.append("")
        lines.append(
            _build_markdown_table(
                ["Month", "Match", "Monthly Best Params", "Trades", "Best RR", "Year RR", "dRR", "Best PnL", "Year PnL", "dPnL"],
                monthly_drift_rows,
            )
        )
    else:
        lines.append("_No monthly drift data available._")

    lines.extend(["", "### Stability Extremes", ""])
    if monthly_year_best_rows:
        stability_rows = []
        for row in monthly_year_best_rows:
            stability_rows.append(
                {
                    "month": str(row[0]),
                    "trades": int(row[1]),
                    "wr": str(row[2]),
                    "total_rr": float(str(row[3])),
                    "pf_rr": str(row[4]),
                    "total_pnl": float(str(row[5]).rstrip("%")),
                    "tp3": int(row[6]),
                    "stop": int(row[7]),
                    "be": int(row[8]),
                    "open": int(row[9]),
                }
            )
        best_months = sorted(
            stability_rows,
            key=lambda row: (row["total_rr"], row["total_pnl"], row["trades"]),
            reverse=True,
        )[:5]
        worst_months = sorted(
            stability_rows,
            key=lambda row: (row["total_rr"], row["total_pnl"], -row["trades"]),
        )[:5]
        lines.append("#### Best Months")
        lines.append("")
        lines.append(
            _build_markdown_table(
                ["Month", "Trades", "WR", "Total RR (net)", "Total PnL (net)", "TP3", "Stop", "BE", "Open"],
                [
                    [
                        row["month"],
                        row["trades"],
                        row["wr"],
                        f"{row['total_rr']:.2f}",
                        f"{row['total_pnl']:.2f}%",
                        row["tp3"],
                        row["stop"],
                        row["be"],
                        row["open"],
                    ]
                    for row in best_months
                ],
            )
        )
        lines.append("")
        lines.append("#### Worst Months")
        lines.append("")
        lines.append(
            _build_markdown_table(
                ["Month", "Trades", "WR", "Total RR (net)", "Total PnL (net)", "TP3", "Stop", "BE", "Open"],
                [
                    [
                        row["month"],
                        row["trades"],
                        row["wr"],
                        f"{row['total_rr']:.2f}",
                        f"{row['total_pnl']:.2f}%",
                        row["tp3"],
                        row["stop"],
                        row["be"],
                        row["open"],
                    ]
                    for row in worst_months
                ],
            )
        )
    else:
        lines.append("_No monthly stability data available._")

    lines.extend(["", "---", "", "## Method Notes", ""])
    lines.append("- `Trades` means eligible stage-4 entries after the RR filter for that parameter set.")
    lines.append(
        f"- All RR and PnL metrics in this report are net of Binance Futures taker fees: "
        f"`{BEE_BITE_STAGE4_TAKER_FEE_RATE * 100.0:.2f}%` on entry and "
        f"`{BEE_BITE_STAGE4_TAKER_FEE_RATE * 100.0:.2f}%` on each exit fill."
    )
    lines.append(f"- Slippage is fixed at `{BEE_BITE_STAGE4_SLIPPAGE_RATE * 100.0:.2f}%` and is currently disabled.")
    lines.append("- `WR` is based on closed profitable trades only; open trades are excluded from win-rate.")
    lines.append("- `Total RR` is the main edge metric; `Total PnL` is still useful but less robust before full portfolio modelling.")
    lines.append("- `PF RR` uses realized RR gains versus realized RR losses.")
    if timed_out_symbols:
        lines.extend(["", "## Timed Out Symbols", ""])
        lines.append(", ".join(f"`{symbol}`" for symbol in timed_out_symbols))
    return "\n".join(lines).strip() + "\n"


def _resolve_stage4_param_key(row: dict[str, object]) -> tuple[float, float, float, float, float]:
    return (
        float(row["minimal_rr"]),
        float(row["tp3_multiplier"]),
        float(row["tp1_share"]),
        float(row["tp2_share"]),
        float(row["tp3_share"]),
    )


def _resolve_stage4_profit_factor_sort_value(value: object) -> float:
    if value is None:
        return 0.0
    numeric = float(value)
    if math.isinf(numeric):
        return 1e18
    return numeric


def _normalize_stage4_metric(values: list[float]) -> list[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if math.isclose(high, low, rel_tol=1e-12, abs_tol=1e-12):
        return [1.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


def _score_stage4_summary_rows(summary_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    eligible_rows = [row for row in summary_rows if float(row.get("eligible_trades", 0) or 0) > 0.0]
    if not eligible_rows:
        for row in summary_rows:
            row["score_total_realized_rr"] = 0.0
            row["score_profit_factor_rr"] = 0.0
            row["score_win_rate"] = 0.0
            row["score_total_pnl_pct"] = 0.0
            row["stage4_score"] = 0.0
        return summary_rows

    rr_values = [float(row.get("total_realized_rr", 0.0) or 0.0) for row in eligible_rows]
    pf_values = [_resolve_stage4_profit_factor_sort_value(row.get("profit_factor_rr")) for row in eligible_rows]
    wr_values = [float(row.get("win_rate", 0.0) or 0.0) for row in eligible_rows]
    pnl_values = [float(row.get("total_pnl_pct", 0.0) or 0.0) for row in eligible_rows]

    normalized_rr = _normalize_stage4_metric(rr_values)
    normalized_pf = _normalize_stage4_metric(pf_values)
    normalized_wr = _normalize_stage4_metric(wr_values)
    normalized_pnl = _normalize_stage4_metric(pnl_values)

    for row, rr_score, pf_score, wr_score, pnl_score in zip(
        eligible_rows,
        normalized_rr,
        normalized_pf,
        normalized_wr,
        normalized_pnl,
        strict=True,
    ):
        row["score_total_realized_rr"] = rr_score
        row["score_profit_factor_rr"] = pf_score
        row["score_win_rate"] = wr_score
        row["score_total_pnl_pct"] = pnl_score
        row["stage4_score"] = (
            (rr_score * 0.45)
            + (pf_score * 0.30)
            + (wr_score * 0.15)
            + (pnl_score * 0.10)
        )

    for row in summary_rows:
        if row not in eligible_rows:
            row["score_total_realized_rr"] = 0.0
            row["score_profit_factor_rr"] = 0.0
            row["score_win_rate"] = 0.0
            row["score_total_pnl_pct"] = 0.0
            row["stage4_score"] = 0.0
    return summary_rows


def _select_best_stage4_summary_row(summary_rows: list[dict[str, object]]) -> dict[str, object] | None:
    eligible_rows = [row for row in summary_rows if float(row.get("eligible_trades", 0) or 0) > 0.0]
    if not eligible_rows:
        return None
    return max(
        eligible_rows,
        key=lambda row: (
            float(row.get("stage4_score", 0.0) or 0.0),
            float(row.get("total_realized_rr", 0.0) or 0.0),
            _resolve_stage4_profit_factor_sort_value(row.get("profit_factor_rr")),
            float(row.get("total_pnl_pct", 0.0) or 0.0),
            float(row.get("win_rate", 0.0) or 0.0),
            -float(row.get("minimal_rr", 0.0) or 0.0),
        ),
    )


def _review_stage2_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("review-stage2", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    logger.info("review-stage2: cache_dir=%s", config.backtest.cache_dir)
    review_timeframe = _resolve_review_timeframe(args)
    results_dir = _resolve_results_dir_for_strategy(config.backtest.results_dir, "bee_bite")
    output_dir = results_dir / "stage2_review" / review_timeframe.value
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = Path(args.output) if getattr(args, "output", None) else output_dir / DEFAULT_STAGE2_EVENTS_OUTPUT_FILE
    plots_dir = output_dir / DEFAULT_STAGE2_PLOTS_DIR_NAME
    failed_plots_dir = output_dir / DEFAULT_FAILED_PLOTS_DIR_NAME
    _reset_review_plot_dir(plots_dir)
    _reset_review_plot_dir(failed_plots_dir)
    plot_limit = int(getattr(args, "plot_limit", 20) or 20)
    plot_scope = _resolve_plot_scope(args, default_scope="all")

    preparer = DataPreparer(config.backtest.cache_dir)
    symbols_raw = args.symbols or preparer.list_symbols(review_timeframe)
    symbols = [normalize_symbol(symbol) for symbol in symbols_raw]
    if not symbols:
        logger.info("review-stage2: no data in cache for tf=%s", review_timeframe.value)
        return 0

    selector = BeeBiteStage1Selector.for_timeframe(review_timeframe)
    detector = BeeBiteStage2Detector()
    plotter = BeeBiteStage2Plotter()
    rows: list[dict[str, object]] = []
    passed_stage2_results: list[tuple[str, _Stage1Regime, BeeBiteStage1Result, BeeBiteStage2Result, pd.DataFrame]] = []
    failed_stage2_results: list[tuple[str, _Stage1Regime, BeeBiteStage1Result, BeeBiteStage2Result, pd.DataFrame]] = []
    reason_counts: Counter[str] = Counter()
    empty_frame_symbols: list[str] = []
    populated_frame_symbols = 0
    frame_cache: dict[tuple[str, Timeframe], pd.DataFrame] = {}
    stage1_review_cache: dict[tuple[str, Timeframe], tuple[list[BeeBiteStage1Result], BeeBiteStage1Result | None]] = {}

    for index, symbol in enumerate(symbols, start=1):
        frame = _get_cached_review_frame(
            cache=frame_cache,
            preparer=preparer,
            symbol=symbol,
            timeframe=review_timeframe,
        )
        if frame.empty:
            empty_frame_symbols.append(symbol)
            continue
        populated_frame_symbols += 1

        stage1_events, stage1_evaluation = _get_cached_stage1_review(
            cache=stage1_review_cache,
            selector=selector,
            symbol=symbol,
            timeframe=review_timeframe,
            frame=frame,
        )
        if not stage1_events:
            reason_counts[f"stage1:{stage1_evaluation.reason if stage1_evaluation is not None else 'unknown'}"] += 1
            continue

        regimes = _resolve_stage1_regime_ends(
            frame=frame,
            regimes=_group_stage1_events_into_regimes(stage1_events),
        )
        for regime in regimes:
            stage1_event = regime.first_event
            stage2_result = detector.detect(
                symbol=symbol,
                frame=frame,
                stage1=stage1_event,
                analysis_end_timestamp=_resolve_stage2_analysis_end_timestamp(frame=frame, regime=regime),
            )
            rows.extend(
                _stage2_result_to_rows(
                    symbol=symbol,
                    timeframe=review_timeframe,
                    regime=regime,
                    stage1_event=stage1_event,
                    stage2_result=stage2_result,
                )
            )
            if stage2_result.passed:
                passed_stage2_results.append((symbol, regime, stage1_event, stage2_result, frame))
            else:
                failed_stage2_results.append((symbol, regime, stage1_event, stage2_result, frame))
                reason_counts[stage2_result.reason] += 1

        if index % _PROGRESS_LOG_EVERY == 0 or index == len(symbols):
            logger.info(
                "review-stage2: progress=%s/%s symbols_with_stage2=%s rows=%s populated_frames=%s empty_frames=%s",
                index,
                len(symbols),
                len({item[0] for item in passed_stage2_results}),
                len(rows),
                populated_frame_symbols,
                len(empty_frame_symbols),
            )

    results_frame = pd.DataFrame(rows)
    if not results_frame.empty:
        sort_columns = [column for column in ("symbol", "regime_index", "row_type", "row_order") if column in results_frame.columns]
        results_frame = results_frame.sort_values(sort_columns).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results_frame.to_csv(output_path, index=False)

    passed_plots_built = 0
    failed_plots_built = 0
    passed_plots_payload = sorted(
        passed_stage2_results,
        key=lambda item: (
            int(item[3].analysis_end_timestamp or 0),
            item[0],
            item[1].regime_index,
        ),
        reverse=True,
    )
    failed_plots_payload = sorted(
        failed_stage2_results,
        key=lambda item: (
            int(item[3].analysis_end_timestamp or 0),
            item[0],
            item[1].regime_index,
        ),
        reverse=True,
    )
    failed_plot_scope = "all" if getattr(args, "plot_scope", None) is None else plot_scope
    for symbol, regime, stage1_event, stage2_result, frame in _select_plot_payload(
        passed_plots_payload,
        plot_scope=plot_scope,
        plot_limit=plot_limit,
    ):
        symbol_slug = symbol.replace("/", "_")
        plot_path = plots_dir / f"{symbol_slug}_stage2_regime_{regime.regime_index:02d}.png"
        plotter.plot_result(
            frame=frame,
            stage1_event=stage1_event,
            stage2_result=stage2_result,
            output_path=plot_path,
            window_end_timestamp=stage2_result.box_end_timestamp or stage2_result.analysis_end_timestamp,
            title_suffix=f"{review_timeframe.value} | regime {regime.regime_index:02d}",
        )
        passed_plots_built += 1
    for symbol, regime, stage1_event, stage2_result, frame in _select_failed_plot_payload(
        failed_plots_payload,
        plot_scope=failed_plot_scope,
        plot_limit=plot_limit,
    ):
        symbol_slug = symbol.replace("/", "_")
        reason_slug = _slugify_reason(stage2_result.reason)
        plot_path = failed_plots_dir / f"{symbol_slug}_stage2_regime_{regime.regime_index:02d}_{reason_slug}.png"
        plotter.plot_result(
            frame=frame,
            stage1_event=stage1_event,
            stage2_result=stage2_result,
            output_path=plot_path,
            window_end_timestamp=stage2_result.analysis_end_timestamp,
            title_suffix=f"{review_timeframe.value} | regime {regime.regime_index:02d} | {stage2_result.reason}",
            dpi=120,
            include_volume=False,
        )
        failed_plots_built += 1

    top_reasons = ", ".join(f"{reason}={count}" for reason, count in reason_counts.most_common())
    if top_reasons:
        logger.info("review-stage2: rejection_reasons %s", top_reasons)
    if empty_frame_symbols:
        logger.warning("review-stage2: empty_frames symbols=%s", ", ".join(empty_frame_symbols[:10]))
    logger.info(
        "review-stage2: symbols=%s symbols_with_stage2=%s csv=%s plots=%s plots_dir=%s plot_scope=%s",
        len(symbols),
        len({item[0] for item in passed_stage2_results}),
        output_path,
        passed_plots_built,
        plots_dir,
        plot_scope,
    )
    logger.info(
        "review-stage2: failed_stage2=%s failed_plots=%s failed_plots_dir=%s",
        len(failed_stage2_results),
        failed_plots_built,
        failed_plots_dir,
    )
    return 0


def _review_stage3_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("review-stage3", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    logger.info("review-stage3: cache_dir=%s", config.backtest.cache_dir)
    review_timeframe = _resolve_review_timeframe(args)
    review_mode = _resolve_stage3_review_mode(args)
    plot_scope = _resolve_plot_scope(args, default_scope="latest")
    results_dir = _resolve_results_dir_for_strategy(config.backtest.results_dir, "bee_bite")
    output_dir = results_dir / "stage3_review" / review_timeframe.value
    output_dir.mkdir(parents=True, exist_ok=True)

    default_output_name = _DEFAULT_STAGE23_BACKTEST_OUTPUT_FILE if review_mode == "evolution" else _DEFAULT_STAGE3_EVENTS_OUTPUT_FILE
    output_path = Path(args.output) if getattr(args, "output", None) else output_dir / default_output_name
    plots_dir = output_dir / _DEFAULT_STAGE3_PLOTS_DIR_NAME
    failed_plots_dir = output_dir / DEFAULT_FAILED_PLOTS_DIR_NAME
    _reset_review_plot_dir(plots_dir)
    _reset_review_plot_dir(failed_plots_dir)
    plot_limit = int(getattr(args, "plot_limit", 20) or 20)

    preparer = DataPreparer(config.backtest.cache_dir)
    symbols_raw = args.symbols or preparer.list_symbols(review_timeframe)
    symbols = [normalize_symbol(symbol) for symbol in symbols_raw]
    if not symbols:
        logger.info("review-stage3: no data in cache for tf=%s", review_timeframe.value)
        return 0

    stage1_timeframe = Timeframe.M15 if review_timeframe != Timeframe.M15 else review_timeframe
    selector = BeeBiteStage1Selector.for_timeframe(stage1_timeframe)
    stage2_detector = BeeBiteStage2Detector()
    stage3_detector = BeeBiteStage3Detector()
    stage2_plotter = BeeBiteStage2Plotter()
    stage3_plotter = BeeBiteStage3Plotter()
    rows: list[dict[str, object]] = []
    passed_stage3_results: list[tuple[str, _Stage1Regime, BeeBiteStage1Result, BeeBiteStage2Result, BeeBiteStage3Result, pd.DataFrame]] = []
    failed_stage3_results: list[tuple[str, _Stage1Regime, BeeBiteStage1Result, BeeBiteStage2Result, BeeBiteStage3Result, pd.DataFrame]] = []
    passed_review_candidates: list[_Stage23ReviewCandidate] = []
    failed_review_candidates: list[_Stage23ReviewCandidate] = []
    reason_counts: Counter[str] = Counter()
    empty_frame_symbols: list[str] = []
    populated_frame_symbols = 0
    frame_cache: dict[tuple[str, Timeframe], pd.DataFrame] = {}
    stage1_review_cache: dict[tuple[str, Timeframe], tuple[list[BeeBiteStage1Result], BeeBiteStage1Result | None]] = {}

    for index, symbol in enumerate(symbols, start=1):
        frame = _get_cached_review_frame(
            cache=frame_cache,
            preparer=preparer,
            symbol=symbol,
            timeframe=review_timeframe,
        )
        if frame.empty:
            empty_frame_symbols.append(symbol)
            continue
        populated_frame_symbols += 1

        stage1_frame = frame if stage1_timeframe == review_timeframe else _get_cached_review_frame(
            cache=frame_cache,
            preparer=preparer,
            symbol=symbol,
            timeframe=stage1_timeframe,
        )
        if stage1_frame.empty:
            reason_counts["stage1:empty_reference_frame"] += 1
            continue

        stage1_events, stage1_evaluation = _get_cached_stage1_review(
            cache=stage1_review_cache,
            selector=selector,
            symbol=symbol,
            timeframe=stage1_timeframe,
            frame=stage1_frame,
        )
        if not stage1_events:
            reason_counts[f"stage1:{stage1_evaluation.reason if stage1_evaluation is not None else 'unknown'}"] += 1
            continue

        regimes = _resolve_stage1_regime_ends(
            frame=stage1_frame,
            regimes=_group_stage1_events_into_regimes(stage1_events),
        )
        for regime in regimes:
            stage1_event = regime.first_event
            dynamic_stage2_result = stage2_detector.detect(
                symbol=symbol,
                frame=frame,
                stage1=stage1_event,
                analysis_end_timestamp=int(regime.regime_end_timestamp),
            )
            if not dynamic_stage2_result.passed:
                rows.extend(
                    _stage3_result_to_rows(
                        symbol=symbol,
                        timeframe=review_timeframe,
                        regime=regime,
                        stage1_event=stage1_event,
                        stage2_result=dynamic_stage2_result,
                        stage3_result=BeeBiteStage3Result(
                            symbol=symbol,
                            passed=False,
                            reason="stage2_not_passed",
                        ),
                    )
                )
                reason_counts["stage2_not_passed"] += 1
                continue

            snapshots: list[_Stage23EvolutionSnapshot] = []
            if review_mode == "evolution":
                snapshots = _build_stage23_evolution_snapshots(
                    symbol=symbol,
                    timeframe=review_timeframe,
                    frame=frame,
                    stage1_event=stage1_event,
                    regime=regime,
                    stage2_detector=stage2_detector,
                    stage3_detector=stage3_detector,
                    initial_stage2_result=dynamic_stage2_result,
                )
                terminal_snapshot = _select_terminal_stage23_snapshot(snapshots)
                stage3_result = terminal_snapshot.stage3_result if terminal_snapshot is not None else BeeBiteStage3Result(
                    symbol=symbol,
                    passed=False,
                    reason="stage2_reference_not_locked",
                )
            else:
                stage3_result = _resolve_stage23_terminal_result(
                    symbol=symbol,
                    timeframe=review_timeframe,
                    frame=frame,
                    stage1_event=stage1_event,
                    regime=regime,
                    stage2_detector=stage2_detector,
                    stage3_detector=stage3_detector,
                    initial_stage2_result=dynamic_stage2_result,
                )
            rows.extend(
                _stage3_result_to_rows(
                    symbol=symbol,
                    timeframe=review_timeframe,
                    regime=regime,
                    stage1_event=stage1_event,
                    stage2_result=dynamic_stage2_result,
                    stage3_result=stage3_result,
                )
            )
            if stage3_result.passed:
                passed_stage3_results.append((symbol, regime, stage1_event, dynamic_stage2_result, stage3_result, frame))
                passed_review_candidates.append(
                    _Stage23ReviewCandidate(
                        symbol=symbol,
                        regime=regime,
                        stage1_event=stage1_event,
                        final_stage2_result=dynamic_stage2_result,
                        final_stage3_result=stage3_result,
                        frame=frame,
                        snapshots=snapshots if review_mode == "evolution" else None,
                    )
                )
            else:
                failed_stage3_results.append((symbol, regime, stage1_event, dynamic_stage2_result, stage3_result, frame))
                failed_review_candidates.append(
                    _Stage23ReviewCandidate(
                        symbol=symbol,
                        regime=regime,
                        stage1_event=stage1_event,
                        final_stage2_result=dynamic_stage2_result,
                        final_stage3_result=stage3_result,
                        frame=frame,
                        snapshots=snapshots if review_mode == "evolution" else None,
                    )
                )
                reason_counts[stage3_result.reason] += 1
            if review_mode == "evolution":
                for snapshot in snapshots:
                    rows.append(
                        _stage23_backtest_row(
                            symbol=symbol,
                            timeframe=review_timeframe,
                            regime=regime,
                            stage1_event=stage1_event,
                            snapshot_order=snapshot.order,
                            snapshot_timestamp=snapshot.timestamp,
                            stage2_result=snapshot.dynamic_stage2_result,
                            stage3_result=snapshot.stage3_result,
                        )
                    )

        if index % _PROGRESS_LOG_EVERY == 0 or index == len(symbols):
            logger.info(
                "review-stage3: progress=%s/%s symbols_with_stage3=%s rows=%s populated_frames=%s empty_frames=%s",
                index,
                len(symbols),
                len({item[0] for item in passed_stage3_results}),
                len(rows),
                populated_frame_symbols,
                len(empty_frame_symbols),
            )

    results_frame = pd.DataFrame(rows)
    if not results_frame.empty:
        sort_columns = [column for column in ("symbol", "regime_index", "row_type", "row_order") if column in results_frame.columns]
        results_frame = results_frame.sort_values(sort_columns).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results_frame.to_csv(output_path, index=False)

    passed_plots_built = 0
    failed_plots_built = 0
    passed_plots_payload = sorted(
        passed_stage3_results,
        key=lambda item: (
            int(item[4].reclaim_timestamp or item[4].break_timestamp or 0),
            item[0],
            item[1].regime_index,
        ),
        reverse=True,
    )
    failed_plots_payload = sorted(
        failed_stage3_results,
        key=lambda item: (
            int(item[4].invalidation_timestamp or item[4].break_timestamp or item[4].analysis_end_timestamp or 0),
            item[0],
            item[1].regime_index,
        ),
        reverse=True,
    )
    selected_passed_snapshot_payload = _select_plot_payload(
        passed_plots_payload,
        plot_scope=plot_scope,
        plot_limit=plot_limit,
    )
    failed_plot_scope = "all" if getattr(args, "plot_scope", None) is None else plot_scope
    selected_failed_snapshot_payload = _select_failed_plot_payload(
        failed_plots_payload,
        plot_scope=failed_plot_scope,
        plot_limit=plot_limit,
    )
    if review_mode == "snapshot":
        for symbol, regime, stage1_event, stage2_result, stage3_result, frame in selected_passed_snapshot_payload:
            symbol_slug = symbol.replace("/", "_")
            plot_path = plots_dir / f"{symbol_slug}_stage3_regime_{regime.regime_index:02d}.png"
            stage3_plotter.plot_result(
                frame=frame,
                stage1_event=stage1_event,
                stage2_result=stage2_result,
                stage3_result=stage3_result,
                output_path=plot_path,
                window_end_timestamp=stage3_result.reclaim_timestamp or stage3_result.analysis_end_timestamp,
                title_suffix=f"{review_timeframe.value} | regime {regime.regime_index:02d}",
            )
            passed_plots_built += 1
        for symbol, regime, stage1_event, stage2_result, stage3_result, frame in selected_failed_snapshot_payload:
            symbol_slug = symbol.replace("/", "_")
            reason_slug = _slugify_reason(stage3_result.reason)
            plot_end_timestamp = (
                stage3_result.reclaim_timestamp
                or stage3_result.invalidation_timestamp
                or stage3_result.break_timestamp
                or stage3_result.analysis_end_timestamp
            )
            plot_path = failed_plots_dir / f"{symbol_slug}_stage3_regime_{regime.regime_index:02d}_{reason_slug}.png"
            stage3_plotter.plot_result(
                frame=frame,
                stage1_event=stage1_event,
                stage2_result=stage2_result,
                stage3_result=stage3_result,
                output_path=plot_path,
                window_end_timestamp=plot_end_timestamp,
                title_suffix=f"{review_timeframe.value} | regime {regime.regime_index:02d} | {stage3_result.reason}",
                dpi=120,
                include_volume=False,
            )
            failed_plots_built += 1
    else:
        passed_evolution_payload = sorted(
            passed_review_candidates,
            key=lambda item: (
                int(
                    item.final_stage3_result.reclaim_timestamp
                    or item.final_stage3_result.break_timestamp
                    or item.final_stage2_result.analysis_end_timestamp
                    or 0
                ),
                item.symbol,
                item.regime.regime_index,
            ),
            reverse=True,
        )
        failed_evolution_payload = sorted(
            failed_review_candidates,
            key=lambda item: (
                int(
                    item.final_stage3_result.break_timestamp
                    or item.final_stage3_result.invalidation_timestamp
                    or item.final_stage3_result.analysis_end_timestamp
                    or item.final_stage2_result.analysis_end_timestamp
                    or 0
                ),
                item.symbol,
                item.regime.regime_index,
            ),
            reverse=True,
        )
        for candidate in _select_plot_payload(
            passed_evolution_payload,
            plot_scope=plot_scope,
            plot_limit=plot_limit,
        ):
            symbol_slug = candidate.symbol.replace("/", "_")
            snapshots = candidate.snapshots or _build_stage23_evolution_snapshots(
                symbol=candidate.symbol,
                timeframe=review_timeframe,
                frame=candidate.frame,
                stage1_event=candidate.stage1_event,
                regime=candidate.regime,
                stage2_detector=stage2_detector,
                stage3_detector=stage3_detector,
                initial_stage2_result=candidate.final_stage2_result,
            )
            for snapshot in snapshots:
                plot_path = plots_dir / (
                    f"{symbol_slug}_stage23_regime_{candidate.regime.regime_index:02d}_step_{snapshot.order:04d}.png"
                )
                if snapshot.reference_stage2_result is not None:
                    stage3_plotter.plot_result(
                        frame=candidate.frame,
                        stage1_event=candidate.stage1_event,
                        stage2_result=snapshot.dynamic_stage2_result,
                        stage3_result=snapshot.stage3_result,
                        output_path=plot_path,
                        window_end_timestamp=snapshot.timestamp,
                        title_suffix=(
                            f"{review_timeframe.value} | regime {candidate.regime.regime_index:02d} "
                            f"| step {snapshot.order:04d} | {snapshot.stage3_result.reason}"
                        ),
                        dpi=100,
                        include_volume=False,
                    )
                else:
                    stage2_plotter.plot_result(
                        frame=candidate.frame,
                        stage1_event=candidate.stage1_event,
                        stage2_result=snapshot.dynamic_stage2_result,
                        output_path=plot_path,
                        window_end_timestamp=snapshot.timestamp,
                        title_suffix=(
                            f"{review_timeframe.value} | regime {candidate.regime.regime_index:02d} "
                            f"| step {snapshot.order:04d} | {snapshot.dynamic_stage2_result.reason}"
                        ),
                        dpi=100,
                        include_volume=False,
                    )
                passed_plots_built += 1
        for candidate in _select_failed_plot_payload(
            failed_evolution_payload,
            plot_scope=failed_plot_scope,
            plot_limit=plot_limit,
        ):
            symbol_slug = candidate.symbol.replace("/", "_")
            reason_slug = _slugify_reason(candidate.final_stage3_result.reason)
            snapshots = candidate.snapshots or _build_stage23_evolution_snapshots(
                symbol=candidate.symbol,
                timeframe=review_timeframe,
                frame=candidate.frame,
                stage1_event=candidate.stage1_event,
                regime=candidate.regime,
                stage2_detector=stage2_detector,
                stage3_detector=stage3_detector,
                initial_stage2_result=candidate.final_stage2_result,
            )
            for snapshot in snapshots:
                plot_path = failed_plots_dir / (
                    f"{symbol_slug}_stage23_regime_{candidate.regime.regime_index:02d}_{reason_slug}_step_{snapshot.order:04d}.png"
                )
                if snapshot.reference_stage2_result is not None:
                    stage3_plotter.plot_result(
                        frame=candidate.frame,
                        stage1_event=candidate.stage1_event,
                        stage2_result=snapshot.dynamic_stage2_result,
                        stage3_result=snapshot.stage3_result,
                        output_path=plot_path,
                        window_end_timestamp=snapshot.timestamp,
                        title_suffix=(
                            f"{review_timeframe.value} | regime {candidate.regime.regime_index:02d} "
                            f"| step {snapshot.order:04d} | {snapshot.stage3_result.reason}"
                        ),
                        dpi=100,
                        include_volume=False,
                    )
                else:
                    stage2_plotter.plot_result(
                        frame=candidate.frame,
                        stage1_event=candidate.stage1_event,
                        stage2_result=snapshot.dynamic_stage2_result,
                        output_path=plot_path,
                        window_end_timestamp=snapshot.timestamp,
                        title_suffix=(
                            f"{review_timeframe.value} | regime {candidate.regime.regime_index:02d} "
                            f"| step {snapshot.order:04d} | {snapshot.dynamic_stage2_result.reason}"
                        ),
                        dpi=100,
                        include_volume=False,
                    )
                failed_plots_built += 1

    top_reasons = ", ".join(f"{reason}={count}" for reason, count in reason_counts.most_common())
    if top_reasons:
        logger.info("review-stage3: rejection_reasons %s", top_reasons)
    if empty_frame_symbols:
        logger.warning("review-stage3: empty_frames symbols=%s", ", ".join(empty_frame_symbols[:10]))
    logger.info(
        "review-stage3: symbols=%s symbols_with_stage3=%s csv=%s plots=%s plots_dir=%s review_mode=%s plot_scope=%s",
        len(symbols),
        len({item[0] for item in passed_stage3_results}),
        output_path,
        passed_plots_built,
        plots_dir,
        review_mode,
        plot_scope,
    )
    logger.info(
        "review-stage3: failed_stage3=%s failed_plots=%s failed_plots_dir=%s",
        len(failed_stage3_results),
        failed_plots_built,
        failed_plots_dir,
    )
    return 0


def _postmortem_stage4_inner(config: AppConfig, args: argparse.Namespace, *, logger_name: str = "postmortem-stage4") -> int:
    logger = get_logger(logger_name, level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    logger.info("postmortem-stage4: cache_dir=%s", config.backtest.cache_dir)
    review_timeframe = _resolve_review_timeframe(args)
    param_grid = _resolve_stage4_param_grid()
    symbol_timeout_seconds = _DEFAULT_STAGE4_SYMBOL_TIMEOUT_SECONDS
    results_dir = _resolve_results_dir_for_strategy(config.backtest.results_dir, "bee_bite")
    output_dir = results_dir / "stage4_postmortem" / review_timeframe.value
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.output) if getattr(args, "output", None) else output_dir / _DEFAULT_STAGE4_POSTMORTEM_OUTPUT_FILE
    overview_path = output_dir / _DEFAULT_STAGE4_GRID_OVERVIEW_OUTPUT_FILE
    report_path = output_dir / _DEFAULT_STAGE4_REPORT_OUTPUT_FILE
    plots_dir = output_dir / "stage4_plots"
    _reset_review_plot_dir(plots_dir)

    preparer = DataPreparer(config.backtest.cache_dir)
    symbols_raw = args.symbols or preparer.list_symbols(review_timeframe)
    symbols = [normalize_symbol(symbol) for symbol in symbols_raw]
    if not symbols:
        logger.info("postmortem-stage4: no data in cache for tf=%s", review_timeframe.value)
        return 0

    rows: list[dict[str, object]] = []
    stage3_passed_count = 0
    plots_built = 0
    empty_frame_symbols: list[str] = []
    timed_out_symbols: list[str] = []
    reason_counts: Counter[str] = Counter()
    progress_started_at = time.perf_counter()

    for index, symbol in enumerate(symbols, start=1):
        symbol_timeout_for_run = None if index == 1 else symbol_timeout_seconds
        logger.info(
            "postmortem-stage4: processing=%s/%s symbol=%s",
            index,
            len(symbols),
            symbol,
        )
        try:
            symbol_result, symbol_error = _run_stage4_symbol_with_timeout(
                cache_dir=config.backtest.cache_dir,
                symbol=symbol,
                review_timeframe=review_timeframe,
                param_grid=param_grid,
                timeout_seconds=symbol_timeout_for_run,
            )
            if symbol_error == "timeout":
                timed_out_symbols.append(symbol)
                reason_counts["symbol_timeout"] += 1
                logger.warning(
                    "postmortem-stage4: symbol_timeout=%ss symbol=%s",
                    symbol_timeout_seconds,
                    symbol,
                )
                continue
            if symbol_error is not None or symbol_result is None:
                reason_counts["symbol_worker_error"] += 1
                logger.warning("postmortem-stage4: symbol_worker_error symbol=%s error=%s", symbol, symbol_error)
                continue
            if bool(symbol_result.get("empty_frame")):
                empty_frame_symbols.append(symbol)
                continue
            rows.extend(cast(list[dict[str, object]], symbol_result.get("rows") or []))
            stage3_passed_count += int(symbol_result.get("stage3_passed_count") or 0)
            reason_counts.update(cast(dict[str, int], symbol_result.get("reason_counts") or {}))
        finally:
            elapsed_seconds = time.perf_counter() - progress_started_at
            progress = (index / len(symbols)) * 100.0 if symbols else 0.0
            eta_seconds = (elapsed_seconds / index) * (len(symbols) - index) if index else 0.0
            logger.info(
                "postmortem-stage4: progress=%s/%s (%.1f%%) eta=%s symbol=%s stage3_passed=%s rows=%s queued_plots=%s",
                index,
                len(symbols),
                progress,
                _format_eta_compact(eta_seconds),
                symbol,
                stage3_passed_count,
                len(rows),
                len(rows),
            )

    summary_rows = _build_stage4_postmortem_summary_rows(
        timeframe=review_timeframe,
        param_grid=param_grid,
        rows=rows,
    )
    summary_rows = _score_stage4_summary_rows(summary_rows)
    best_summary_row = _select_best_stage4_summary_row(summary_rows)
    best_param_key = _resolve_stage4_param_key(best_summary_row) if best_summary_row is not None else None
    for summary_row in summary_rows:
        summary_row["is_best"] = bool(best_param_key is not None and _resolve_stage4_param_key(summary_row) == best_param_key)

    selected_trade_rows = [
        trade_row
        for trade_row in rows
        if trade_row.get("row_type") == "trade"
        and bool(trade_row.get("eligible"))
        and best_param_key is not None
        and _resolve_stage4_param_key(cast(dict[str, object], trade_row)) == best_param_key
    ]
    for trade_row in selected_trade_rows:
        symbol = cast(str, trade_row["symbol"])
        regime_index = int(trade_row["regime_index"])
        plot_timeout_for_run = None if symbol == symbols[0] else symbol_timeout_seconds
        plot_error = _run_stage4_plot_with_timeout(
            cache_dir=config.backtest.cache_dir,
            symbol=symbol,
            review_timeframe=review_timeframe,
            regime_index=regime_index,
            reference_box_end_timestamp=(
                int(trade_row["reference_box_end_timestamp"])
                if trade_row.get("reference_box_end_timestamp") is not None
                else None
            ),
            trade_row=cast(dict[str, object], trade_row),
            output_path=plots_dir / (
                f"{symbol.replace('/', '_')}_stage4_regime_{regime_index:02d}"
                f"_rr_{float(trade_row['minimal_rr']):.2f}"
                f"_m_{float(trade_row['tp3_multiplier']):.1f}"
                f"_w_{int(round(float(trade_row['tp1_share']) * 100.0))}"
                f"_{int(round(float(trade_row['tp2_share']) * 100.0))}"
                f"_{int(round(float(trade_row['tp3_share']) * 100.0))}.png"
            ),
            timeout_seconds=plot_timeout_for_run,
        )
        if plot_error is not None:
            if plot_error == "timeout":
                timed_out_symbols.append(f"{symbol}#plot")
                reason_counts["plot_timeout"] += 1
                logger.warning(
                    "postmortem-stage4: plot_timeout=%ss symbol=%s regime=%s",
                    symbol_timeout_seconds,
                    symbol,
                    regime_index,
                )
            else:
                reason_counts["plot_rebuild_error"] += 1
                logger.warning(
                    "postmortem-stage4: skipped_plot_rebuild symbol=%s regime=%s error=%s",
                    symbol,
                    regime_index,
                    plot_error,
                )
            continue
        plots_built += 1

    results_frame = pd.DataFrame([*summary_rows, *rows])
    if not results_frame.empty:
        sort_columns = [column for column in ("row_type", "row_order", "symbol", "regime_index") if column in results_frame.columns]
        results_frame = results_frame.sort_values(sort_columns).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    overview_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    results_frame.to_csv(output_path, index=False)
    pd.DataFrame(summary_rows).to_csv(overview_path, index=False)
    report_path.write_text(
        _render_stage4_postmortem_report(
            timeframe=review_timeframe,
            symbols_count=len(symbols),
            stage3_passed_count=stage3_passed_count,
            param_grid=param_grid,
            summary_rows=summary_rows,
            trade_rows=rows,
            reason_counts=reason_counts,
            timed_out_symbols=timed_out_symbols,
        ),
        encoding="utf-8",
    )

    top_reasons = ", ".join(f"{reason}={count}" for reason, count in reason_counts.most_common())
    if top_reasons:
        logger.info("postmortem-stage4: rejection_reasons %s", top_reasons)
    if empty_frame_symbols:
        logger.warning("postmortem-stage4: empty_frames symbols=%s", ", ".join(empty_frame_symbols[:10]))
    if timed_out_symbols:
        logger.warning(
            "postmortem-stage4: timed_out_symbols=%s list=%s",
            len(timed_out_symbols),
            ", ".join(timed_out_symbols[:10]),
        )
    logger.info(
        "postmortem-stage4: symbols=%s stage3_passed=%s trades=%s plots=%s grid_size=%s best=%s csv=%s overview=%s report=%s",
        len(symbols),
        stage3_passed_count,
        len(rows),
        plots_built,
        len(param_grid),
        (
            f"rr={float(best_summary_row['minimal_rr']):.2f},"
            f"m={float(best_summary_row['tp3_multiplier']):.1f},"
            f"w={float(best_summary_row['tp1_share']):.2f}/"
            f"{float(best_summary_row['tp2_share']):.2f}/"
            f"{float(best_summary_row['tp3_share']):.2f},"
            f"score={float(best_summary_row['stage4_score']):.4f}"
            if best_summary_row is not None else "n/a"
        ),
        output_path,
        overview_path,
        report_path,
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
    timeframes = _resolve_review_timeframes(args)
    if len(timeframes) == 1:
        return _run_with_logging("review-stage1", config, lambda: _review_stage1_inner(config, args))

    exit_codes: list[int] = []
    base_output = Path(args.output) if getattr(args, "output", None) else None
    for timeframe in timeframes:
        scoped_args = _with_review_timeframe(
            args,
            timeframe,
            output_path=_append_timeframe_suffix(base_output, timeframe) if base_output is not None else None,
        )
        exit_codes.append(
            _run_with_logging(
                f"review-stage1[{timeframe.value}]",
                config,
                lambda stage_args=scoped_args: _review_stage1_inner(config, stage_args),
            )
        )
    return max(exit_codes, default=0)


def review_stage2(config: AppConfig, args: argparse.Namespace) -> int:
    """Находит stage-2 структуру и сохраняет review-артефакты."""
    timeframes = _resolve_review_timeframes(args)
    if len(timeframes) == 1:
        return _run_with_logging("review-stage2", config, lambda: _review_stage2_inner(config, args))

    exit_codes: list[int] = []
    base_output = Path(args.output) if getattr(args, "output", None) else None
    for timeframe in timeframes:
        scoped_args = _with_review_timeframe(
            args,
            timeframe,
            output_path=_append_timeframe_suffix(base_output, timeframe) if base_output is not None else None,
        )
        exit_codes.append(
            _run_with_logging(
                f"review-stage2[{timeframe.value}]",
                config,
                lambda stage_args=scoped_args: _review_stage2_inner(config, stage_args),
            )
        )
    return max(exit_codes, default=0)


def review_stage3(config: AppConfig, args: argparse.Namespace) -> int:
    """Находит stage-3 sweep+reclaim и сохраняет review-артефакты."""
    timeframes = _resolve_review_timeframes(args)
    if len(timeframes) == 1:
        return _run_with_logging("review-stage3", config, lambda: _review_stage3_inner(config, args))

    exit_codes: list[int] = []
    base_output = Path(args.output) if getattr(args, "output", None) else None
    for timeframe in timeframes:
        scoped_args = _with_review_timeframe(
            args,
            timeframe,
            output_path=_append_timeframe_suffix(base_output, timeframe) if base_output is not None else None,
        )
        exit_codes.append(
            _run_with_logging(
                f"review-stage3[{timeframe.value}]",
                config,
                lambda stage_args=scoped_args: _review_stage3_inner(config, stage_args),
            )
        )
    return max(exit_codes, default=0)


def postmortem_stage4(config: AppConfig, args: argparse.Namespace) -> int:
    """Собирает stage-4 postmortem по валидным stage-3 setups и сетке minimal_rr."""
    timeframes = _resolve_review_timeframes(args)
    if len(timeframes) == 1:
        return _run_with_logging("postmortem-stage4", config, lambda: _postmortem_stage4_inner(config, args, logger_name="postmortem-stage4"))

    exit_codes: list[int] = []
    base_output = Path(args.output) if getattr(args, "output", None) else None
    for timeframe in timeframes:
        scoped_args = _with_review_timeframe(
            args,
            timeframe,
            output_path=_append_timeframe_suffix(base_output, timeframe) if base_output is not None else None,
        )
        exit_codes.append(
            _run_with_logging(
                f"postmortem-stage4[{timeframe.value}]",
                config,
                lambda stage_args=scoped_args, command_name=f"postmortem-stage4[{timeframe.value}]": _postmortem_stage4_inner(config, stage_args, logger_name=command_name),
            )
        )
    return max(exit_codes, default=0)


def check_quality(config: AppConfig, args: argparse.Namespace) -> int:
    """Проверяет качество и целостность данных."""
    return _run_with_logging("check-quality", config, lambda: _check_quality_inner(config, args))


def clear_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Очищает директорию локального кэша и пересоздаёт её."""
    return _run_with_logging("clear-cache", config, lambda: _clear_cache_inner(config, args))



