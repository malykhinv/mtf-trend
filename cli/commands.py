"""ÐœÐ¾Ð´ÑƒÐ»ÑŒ Ð¿Ñ€Ð¾ÐµÐºÑ‚Ð°."""

from __future__ import annotations

import argparse
import csv
import concurrent.futures
import json
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from logging import Logger
from pathlib import Path
from typing import Callable, cast

import numpy as np
import pandas as pd

from config import AppConfig
from constants import (
    DEFAULT_BACKTEST_OUTPUT_FILE,
    DEFAULT_BEE_BITE_DEPOSIT,
    DEFAULT_BEE_BITE_RISK_PCT,
    DEFAULT_RESULTS_DIR,
    DEFAULT_QUALITY_REPORT_OUTPUT_FILE,
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
    LOG_MSG_TASK_COMPLETED,
)
from data.clients.noop_market_data_client import NoOpMarketDataClient
from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.fetchers.market_data_fetcher import MarketDataFetcher
from data.fetchers.ohlcv_fetcher import OhlcvFetcher
from data.fetchers.oi_fetcher import OiFetcher
from data.liquidity.bee_bite_stage1_selector import BeeBiteStage1Result, BeeBiteStage1Selector
from data.liquidity.daily_volume_ranker import DailyVolumeRanker
from data.quality.data_validator import DataValidator
from data.quality.gap_detector import GapDetector
from data.storage.parquet_storage import ParquetStorage
from domain.enums.entry_trigger import EntryTrigger
from domain.enums.exchange import Exchange
from domain.enums.timeframe import Timeframe
from domain.models.reporting.quality_report import QualityReport
from domain.models.reporting.quality_summary import QualitySummary
from domain.models.reporting.quality_symbol_stats import QualitySymbolStats
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult
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
from strategy.post_pump_absorption import (
    PostPumpAbsorptionParams,
    PostPumpAbsorptionStrategy,
    parse_post_pump_absorption_profile_id,
)
from strategy.post_pump_absorption.config import (
    POST_PUMP_ABSORPTION_DEFAULT_TIMEFRAME,
    POST_PUMP_ABSORPTION_SUPPORTED_ENTRY_TIMEFRAMES,
)
from strategy.post_pump_absorption.research import (
    PostPumpAbsorptionResearchRun,
    build_post_pump_absorption_research_artifacts,
    load_post_pump_absorption_research_run,
)
from utils.logger import get_logger
from utils.symbols import normalize_symbol
from vectorbt_runner import BacktestRunner, DataPreparer, SymbolMtfFrames

# region ÐŸÑ€Ð¸Ð²Ð°Ñ‚Ð½Ñ‹Ðµ

_PROGRESS_LOG_EVERY = 100


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
        if normalized not in {"bee_bite", "post_pump_absorption"}:
            raise ValueError(f"ÐÐµÐ¿Ð¾Ð´Ð´ÐµÑ€Ð¶Ð¸Ð²Ð°ÐµÐ¼Ñ‹Ð¹ strategy_id: {normalized}")
        return normalized
    return "bee_bite"


def _resolve_results_dir_for_strategy(base_results_dir: Path, strategy_id: str) -> Path:
    if strategy_id not in {"bee_bite", "post_pump_absorption"}:
        raise ValueError(f"ÐÐµÐ¿Ð¾Ð´Ð´ÐµÑ€Ð¶Ð¸Ð²Ð°ÐµÐ¼Ñ‹Ð¹ strategy_id: {strategy_id}")
    return base_results_dir / "strategy" / strategy_id



def _build_futures_symbol_map(symbols: list[str]) -> dict[str, str]:
    return {
        normalize_symbol(symbol): symbol
        for symbol in symbols
    }


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

    def _optional_float(column_name: str) -> float | None:
        raw_value = row.get(column_name)
        return None if _is_missing_scalar(raw_value) else float(raw_value)

    bite_t_max_in_trade_raw = row.get("bite_t_max_in_trade")
    bite_t_max_in_trade = None if _is_missing_scalar(bite_t_max_in_trade_raw) else int(bite_t_max_in_trade_raw)
    if bite_t_max_in_trade is not None and bite_t_max_in_trade < 1:
        raise ValueError("Ð¿Ð°Ñ€Ð°Ð¼ÐµÑ‚Ñ€ bite_t_max_in_trade Ð´Ð¾Ð»Ð¶ÐµÐ½ Ð±Ñ‹Ñ‚ÑŒ >= 1 Ð¸Ð»Ð¸ None")
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
    bite_deposit = _optional_float("bite_deposit") or DEFAULT_BEE_BITE_DEPOSIT
    bite_risk_pct = _optional_float("bite_risk_pct")
    bite_r_trade = _optional_float("bite_r_trade")
    if bite_risk_pct is None:
        bite_risk_pct = (bite_r_trade / bite_deposit) if bite_r_trade is not None else DEFAULT_BEE_BITE_RISK_PCT
    resolved_trade_risk = bite_r_trade if bite_r_trade is not None else bite_deposit * bite_risk_pct

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
        bite_deposit=bite_deposit,
        bite_risk_pct=bite_risk_pct,
        bite_r_trade=resolved_trade_risk,
        bite_portfolio_risk_limit=float(row["bite_portfolio_risk_limit"]),
        bite_min_stop_atr_ratio=float(row["bite_min_stop_atr_ratio"]),
        bite_t_max_in_trade=bite_t_max_in_trade,
        bite_profile_id=parse_bee_bite_profile_id(_normalize_enum_raw(bite_profile_id_raw), default="A"),
        bite_grid_mode=parse_bee_bite_grid_mode(_normalize_enum_raw(bite_grid_mode_raw), default="baseline"),
    )
def _build_post_pump_absorption_params_from_row(
    row: pd.Series,
    *,
    symbol: str,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
) -> PostPumpAbsorptionParams:
    def _is_missing_scalar(value: object) -> bool:
        if value is None or value is pd.NA:
            return True
        if isinstance(value, (pd.Series, pd.DataFrame)):
            return False
        if isinstance(value, float):
            return bool(pd.isna(value))
        return False

    def _optional_float(column_name: str) -> float | None:
        raw_value = row.get(column_name)
        return None if _is_missing_scalar(raw_value) else float(raw_value)

    deposit = _optional_float("ppa_deposit") or DEFAULT_BEE_BITE_DEPOSIT
    risk_pct = _optional_float("ppa_risk_pct")
    r_trade = _optional_float("ppa_r_trade")
    profile_raw = row.get("ppa_profile_id")
    profile_id = (
        parse_post_pump_absorption_profile_id(None)
        if _is_missing_scalar(profile_raw)
        else parse_post_pump_absorption_profile_id(str(profile_raw))
    )
    if risk_pct is None:
        risk_pct = (r_trade / deposit) if r_trade is not None else DEFAULT_BEE_BITE_RISK_PCT
    resolved_trade_risk = r_trade if r_trade is not None else deposit * risk_pct

    return PostPumpAbsorptionParams(
        profile_id=profile_id,
        symbol=symbol,
        grid_variant_id=str(row.get("ppa_grid_variant_id", "baseline")),
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        atr_window_minutes=int(row["ppa_atr_window_minutes"]),
        pump_window_minutes=int(row["ppa_pump_window_minutes"]),
        pump_baseline_window_minutes=int(row["ppa_pump_baseline_window_minutes"]),
        pump_min_move_atr=float(row["ppa_pump_min_move_atr"]),
        pump_volume_mult=float(row["ppa_pump_volume_mult"]),
        range_min_minutes=int(row["ppa_range_min_minutes"]),
        range_max_minutes=int(row["ppa_range_max_minutes"]),
        lower_zone_fraction=float(row["ppa_lower_zone_fraction"]),
        max_range_width_atr=float(row["ppa_max_range_width_atr"]),
        max_range_width_pump_fraction=float(row["ppa_max_range_width_pump_fraction"]),
        taker_ratio_threshold=float(row["ppa_taker_ratio_threshold"]),
        taker_volume_mult=float(row["ppa_taker_volume_mult"]),
        flow_baseline_window_minutes=int(row["ppa_flow_baseline_window_minutes"]),
        structure_break_minutes=int(row["ppa_structure_break_minutes"]),
        micro_base_minutes=int(row["ppa_micro_base_minutes"]),
        micro_base_max_width_atr=float(row["ppa_micro_base_max_width_atr"]),
        entry_break_buffer_atr=float(row["ppa_entry_break_buffer_atr"]),
        stop_buffer_atr=float(row["ppa_stop_buffer_atr"]),
        min_stop_atr=float(row["ppa_min_stop_atr"]),
        max_stop_atr=float(row["ppa_max_stop_atr"]),
        max_stop_range_fraction=float(row["ppa_max_stop_range_fraction"]),
        max_entry_range_fraction=float(row["ppa_max_entry_range_fraction"]),
        tp1_share=float(row["ppa_tp1_share"]),
        be_buffer_pct=float(row["ppa_be_buffer_pct"]),
        time_exit_minutes=int(row["ppa_time_exit_minutes"]),
        ppa_deposit=deposit,
        ppa_risk_pct=risk_pct,
        ppa_r_trade=resolved_trade_risk,
    )


def _flatten_trade_for_diagnostics(trade: object) -> dict[str, object]:
    payload = {
        "entry_timestamp_ms": int(getattr(trade, "entry_timestamp_ms")),
        "exit_timestamp_ms": int(getattr(trade, "exit_timestamp_ms")),
        "pnl": float(getattr(trade, "pnl")),
        "pnl_percent": float(getattr(getattr(trade, "pnl_percent"), "value", getattr(trade, "pnl_percent"))),
        "result_type": str(getattr(getattr(trade, "result_type"), "value", getattr(trade, "result_type"))),
    }
    metadata = getattr(trade, "metadata", None)
    if isinstance(metadata, dict):
        payload.update(metadata)
    return payload


def _plot_post_pump_absorption_diagnostics_for_symbols(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
    strategy: PostPumpAbsorptionStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    params_row: pd.Series,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    log_prefix: str,
) -> None:
    output_dir = Path(getattr(args, "output_dir", None) or (config.backtest.results_dir / "trade_plots"))
    diagnostics_dir = output_dir / "post_pump_absorption_diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    symbols_with_trades = 0
    total_trades_generated = 0

    for symbol, mtf_frames in symbol_frames.items():
        params = _build_post_pump_absorption_params_from_row(
            params_row,
            symbol=symbol,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
        )
        trades = strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)
        diagnostics = strategy.consume_last_generation_diagnostics()
        trade_rows = [_flatten_trade_for_diagnostics(trade) for trade in trades]
        total_trades_generated += len(trade_rows)
        if trade_rows:
            symbols_with_trades += 1

        payload = {
            "symbol": symbol,
            "trades_generated": len(trade_rows),
            "diagnostics": diagnostics,
            "trades": trade_rows,
        }
        base_name = symbol.replace("/", "_")
        (diagnostics_dir / f"{base_name}_diagnostics.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        pd.DataFrame(trade_rows).to_csv(diagnostics_dir / f"{base_name}_trades.csv", index=False)

    logger.info(
        "%s: ÑÐ¾Ñ…Ñ€Ð°Ð½ÐµÐ½Ð° Ð´Ð¸Ð°Ð³Ð½Ð¾ÑÑ‚Ð¸ÐºÐ° post_pump_absorption symbols=%s trades_generated=%s output_dir=%s",
        log_prefix,
        symbols_with_trades,
        total_trades_generated,
        diagnostics_dir,
    )
    if symbols_with_trades == 0:
        logger.warning("%s: Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾ ÑÐ´ÐµÐ»Ð¾Ðº post_pump_absorption Ð´Ð»Ñ Ð´Ð¸Ð°Ð³Ð½Ð¾ÑÑ‚Ð¸Ñ‡ÐµÑÐºÐ¾Ð³Ð¾ Ð²Ñ‹Ð²Ð¾Ð´Ð°", log_prefix)


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
        "%s: ÑÐ¾Ñ…Ñ€Ð°Ð½ÐµÐ½Ð° Ð´Ð¸Ð°Ð³Ð½Ð¾ÑÑ‚Ð¸Ñ‡ÐµÑÐºÐ°Ñ Ð²Ð¸Ð·ÑƒÐ°Ð»Ð¸Ð·Ð°Ñ†Ð¸Ñ bee_bite symbols=%s trades_generated=%s output_dir=%s",
        log_prefix,
        symbols_with_states,
        total_trades_generated,
        diagnostics_dir,
    )
    if symbols_with_states == 0:
        logger.warning("%s: Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾ Ð´Ð¸Ð°Ð³Ð½Ð¾ÑÑ‚Ð¸Ñ‡ÐµÑÐºÐ¸Ñ… Ð´Ð°Ð½Ð½Ñ‹Ñ… bee_bite Ð´Ð»Ñ Ð²Ð¸Ð·ÑƒÐ°Ð»Ð¸Ð·Ð°Ñ†Ð¸Ð¸", log_prefix)


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
            logger.error("%s: Ð½ÐµÐ¿Ð¾Ð´Ð´ÐµÑ€Ð¶Ð¸Ð²Ð°ÐµÐ¼Ñ‹Ð¹ Ñ‚Ð¸Ð¿ Ð²Ð¸Ð·ÑƒÐ°Ð»Ð¸Ð·Ð°Ñ†Ð¸Ð¸ Ð´Ð»Ñ ÑÑ‚Ñ€Ð°Ñ‚ÐµÐ³Ð¸Ð¸ bee_bite", log_prefix)
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

    logger.error("%s: Ð²Ð¸Ð·ÑƒÐ°Ð»Ð¸Ð·Ð°Ñ†Ð¸Ñ Ð½Ðµ Ð¿Ð¾Ð´Ð´ÐµÑ€Ð¶Ð¸Ð²Ð°ÐµÑ‚ÑÑ Ð´Ð»Ñ ÑÑ‚Ñ€Ð°Ñ‚ÐµÐ³Ð¸Ð¸ %s", log_prefix, strategy_id)
    return False


def _plot_for_strategy_dispatch(
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
    if strategy_id == "post_pump_absorption":
        if not isinstance(strategy, PostPumpAbsorptionStrategy):
            logger.error("%s: unsupported visualization type for post_pump_absorption", log_prefix)
            return False
        _plot_post_pump_absorption_diagnostics_for_symbols(
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

    return _plot_for_strategy(
        config=config,
        args=args,
        logger=logger,
        strategy_id=strategy_id,
        strategy=strategy,
        symbol_frames=symbol_frames,
        params_row=params_row,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        log_prefix=log_prefix,
    )


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
        logger.error("plot-from-results: Ñ„Ð°Ð¹Ð» Ñ€ÐµÐ·ÑƒÐ»ÑŒÑ‚Ð°Ñ‚Ð¾Ð² Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½: %s", csv_path)
        return None

    frame = pd.read_csv(csv_path)
    if frame.empty:
        logger.error("plot-from-results: Ñ„Ð°Ð¹Ð» Ñ€ÐµÐ·ÑƒÐ»ÑŒÑ‚Ð°Ñ‚Ð¾Ð² Ð¿ÑƒÑÑ‚Ð¾Ð¹: %s", csv_path)
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
        "post_pump_absorption": [
            "ppa_profile_id",
            "ppa_atr_window_minutes",
            "ppa_pump_window_minutes",
            "ppa_pump_baseline_window_minutes",
            "ppa_pump_min_move_atr",
            "ppa_pump_volume_mult",
            "ppa_range_min_minutes",
            "ppa_range_max_minutes",
            "ppa_lower_zone_fraction",
            "ppa_max_range_width_atr",
            "ppa_max_range_width_pump_fraction",
            "ppa_taker_ratio_threshold",
            "ppa_taker_volume_mult",
            "ppa_flow_baseline_window_minutes",
            "ppa_structure_break_minutes",
            "ppa_micro_base_minutes",
            "ppa_micro_base_max_width_atr",
            "ppa_entry_break_buffer_atr",
            "ppa_stop_buffer_atr",
            "ppa_min_stop_atr",
            "ppa_max_stop_atr",
            "ppa_max_stop_range_fraction",
            "ppa_max_entry_range_fraction",
            "ppa_tp1_share",
            "ppa_be_buffer_pct",
            "ppa_time_exit_minutes",
            "ppa_r_trade",
        ],
    }
    required_columns = required_columns_by_strategy.get(strategy_id)
    if required_columns is None:
        logger.error("plot-from-results: Ð½ÐµÐ¿Ð¾Ð´Ð´ÐµÑ€Ð¶Ð¸Ð²Ð°ÐµÐ¼Ð°Ñ ÑÑ‚Ñ€Ð°Ñ‚ÐµÐ³Ð¸Ñ %s", strategy_id)
        return None
    missing_columns = [column for column in required_columns if column not in frame.columns]
    if missing_columns:
        logger.error("plot-from-results: Ð¾Ñ‚ÑÑƒÑ‚ÑÑ‚Ð²ÑƒÑŽÑ‚ Ð¾Ð±ÑÐ·Ð°Ñ‚ÐµÐ»ÑŒÐ½Ñ‹Ðµ ÐºÐ¾Ð»Ð¾Ð½ÐºÐ¸: %s", ", ".join(missing_columns))
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
                    "plot-from-results: Ð½Ð°Ð¹Ð´ÐµÐ½Ð° ÐºÐ¾Ð¼Ð±Ð¸Ð½Ð°Ñ†Ð¸Ñ Ð¿Ð¾ ÐºÐ¾Ð»Ð¾Ð½ÐºÐµ %s, id=%s, ÑÐ¾Ð²Ð¿Ð°Ð´ÐµÐ½Ð¸Ð¹=%s",
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
                    "plot-from-results: id=%s Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½ (Ð½ÐµÑ‚ ÐºÐ¾Ð»Ð¾Ð½Ð¾Ðº id/combination_id/rank Ð¸ Ð½Ð¾Ð¼ÐµÑ€ ÑÑ‚Ñ€Ð¾ÐºÐ¸ Ð²Ð½Ðµ Ð´Ð¸Ð°Ð¿Ð°Ð·Ð¾Ð½Ð° 1..%s)",
                    selected_id,
                    len(frame),
                )
                return None
            selected_row = frame.iloc[row_index]
            logger.warning(
                "plot-from-results: id=%s Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½ Ð² id/combination_id/rank, Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ð½ 1-based Ð½Ð¾Ð¼ÐµÑ€ ÑÑ‚Ñ€Ð¾ÐºÐ¸=%s",
                selected_id,
                selected_id,
            )
    else:
        sorted_frame = frame.sort_values(["profit_factor", "trades_count"], ascending=[False, False], na_position="last")
        selected_row = sorted_frame.iloc[0]

    logger.info(
        "plot-from-results: Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ð½Ñ‹ Ð¿Ð°Ñ€Ð°Ð¼ÐµÑ‚Ñ€Ñ‹ Ð¸Ð· %s (pf=%s, trades_count=%s, id=%s)",
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
    logger.info(f"{command_name}: ÑÑ‚Ð°Ñ€Ñ‚")
    try:
        code = body()
        logger.info(f"{LOG_MSG_TASK_COMPLETED % command_name} (ÐºÐ¾Ð´={code})")
        return code
    except Exception as exc:
        logger.exception(f"{command_name}: Ð¾ÑˆÐ¸Ð±ÐºÐ°: {exc}")
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
    futures_symbol_map = _build_futures_symbol_map(futures_symbols_raw)
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
            '??????-????????: ????? ?? ???????? ??????????? ????? ?????????? (%s)',
            exc,
        )

    combined_symbols = set(liquid_symbols) | exchange_liquid_symbols
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
        len(exchange_liquid_symbols - set(liquid_symbols)),
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
            "%s: ÑÐ²Ð¾Ð´ÐºÐ° Ð·Ð°Ð³Ñ€ÑƒÐ·ÐºÐ¸ Ð²ÑÐµÐ³Ð¾=%s ÑƒÑÐ¿ÐµÑˆÐ½Ð¾=%s Ñ Ð¾ÑˆÐ¸Ð±ÐºÐ°Ð¼Ð¸=%s Ð´Ð¾Ð»Ñ_Ð¾ÑˆÐ¸Ð±Ð¾Ðº=%.2f%%",
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
        "loaded": "Ð—Ð°Ð³Ñ€ÑƒÐ¶ÐµÐ½Ð¾ %s Ð¼Ð¾Ð½ÐµÑ‚.",
        "updated": "ÐžÐ±Ð½Ð¾Ð²Ð»ÐµÐ½Ð¾ %s Ð¼Ð¾Ð½ÐµÑ‚.",
    }
    template = templates.get(action)
    if template is None:
        raise ValueError(f"ÐÐµÐ¿Ð¾Ð´Ð´ÐµÑ€Ð¶Ð¸Ð²Ð°ÐµÐ¼Ð¾Ðµ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ðµ: {action}")
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
    raise ValueError(f"ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ð¾Ðµ Ð·Ð½Ð°Ñ‡ÐµÐ½Ð¸Ðµ {argument_name}: {value}. ÐŸÐ¾Ð´Ð´ÐµÑ€Ð¶Ð¸Ð²Ð°ÐµÐ¼Ñ‹Ðµ Ð·Ð½Ð°Ñ‡ÐµÐ½Ð¸Ñ: {supported}")


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
        fallback_timeframe=Timeframe.M15,
        argument_name="--timeframes",
    )


def _resolve_ppa_research_timeframes(args: argparse.Namespace) -> tuple[Timeframe, ...]:
    return _resolve_timeframe_sequence(
        getattr(args, "timeframes", None),
        fallback=POST_PUMP_ABSORPTION_SUPPORTED_ENTRY_TIMEFRAMES,
        fallback_timeframe=POST_PUMP_ABSORPTION_DEFAULT_TIMEFRAME,
        argument_name="--timeframes",
        supported=set(POST_PUMP_ABSORPTION_SUPPORTED_ENTRY_TIMEFRAMES),
    )


def _resolve_backtest_timeframes(
    *,
    strategy_id: str,
    args: argparse.Namespace,
    configured_levels_timeframe: Timeframe,
    configured_entry_timeframe: Timeframe,
) -> tuple[Timeframe, Timeframe]:
    if strategy_id != "post_pump_absorption":
        levels_timeframe = _resolve_timeframe(
            getattr(args, "levels_tf", None),
            fallback=configured_levels_timeframe,
            argument_name="--levels-tf",
        )
        entry_timeframe = _resolve_timeframe(
            getattr(args, "entry_tf", None),
            fallback=configured_entry_timeframe,
            argument_name="--entry-tf",
        )
        return levels_timeframe, entry_timeframe

    fallback_entry = configured_entry_timeframe
    if fallback_entry not in POST_PUMP_ABSORPTION_SUPPORTED_ENTRY_TIMEFRAMES:
        fallback_entry = POST_PUMP_ABSORPTION_DEFAULT_TIMEFRAME

    entry_timeframe = _resolve_timeframe(
        getattr(args, "entry_tf", None),
        fallback=fallback_entry,
        argument_name="--entry-tf",
    )
    if entry_timeframe not in POST_PUMP_ABSORPTION_SUPPORTED_ENTRY_TIMEFRAMES:
        supported_values = ", ".join(tf.value for tf in POST_PUMP_ABSORPTION_SUPPORTED_ENTRY_TIMEFRAMES)
        raise ValueError(
            "post_pump_absorption supports only micro timeframes "
            f"{{{supported_values}}}, got {entry_timeframe.value}"
        )

    levels_fallback = entry_timeframe if getattr(args, "levels_tf", None) is None else configured_levels_timeframe
    levels_timeframe = _resolve_timeframe(
        getattr(args, "levels_tf", None),
        fallback=levels_fallback,
        argument_name="--levels-tf",
    )
    if levels_timeframe != entry_timeframe:
        raise ValueError(
            "post_pump_absorption currently supports only single-timeframe execution: "
            "--levels-tf must match --entry-tf"
        )
    return levels_timeframe, entry_timeframe


def _with_ppa_research_timeframe(args: argparse.Namespace, timeframe: Timeframe) -> argparse.Namespace:
    cloned = argparse.Namespace(**vars(args))
    cloned.command = "run-backtest"
    cloned.strategy = "post_pump_absorption"
    cloned.entry_tf = timeframe.value
    cloned.levels_tf = timeframe.value
    cloned.plot = True
    cloned.plot_from_results = False
    cloned.results_input = None
    cloned.id = None
    cloned.bee_bite_grid = None
    cloned.bee_bite_reclaim_mode = None
    cloned.bee_bite_retest_mode = None
    cloned.bee_bite_cooldown_hours = None
    cloned.bee_bite_max_age_range_hours = None
    cloned.bee_bite_deposit = None
    cloned.bee_bite_risk_pct = None
    return cloned


def _fetch_data_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("fetch-data", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    fetch_timeframes = _resolve_fetch_timeframes(args, config.fetch.timeframes)

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
        "Ð·Ð°Ð³Ñ€ÑƒÐ·ÐºÐ°-Ð´Ð°Ð½Ð½Ñ‹Ñ…: Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾ Ñ„ÑŒÑŽÑ‡ÐµÑ€ÑÐ¾Ð²=%s Ð²Ñ‹Ð±Ñ€Ð°Ð½Ð¾_ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð²=%s (Ñ€ÐµÐ¶Ð¸Ð¼_Ð¿Ð¾Ð´Ð±Ð¾Ñ€Ð°=%s)",
        all_futures_count,
        len(symbols),
        "cache+exchange-liquidity",
    )
    if not symbols:
        liquidity_quality_by_symbol = {}
        logger.info("Ð·Ð°Ð³Ñ€ÑƒÐ·ÐºÐ°-Ð´Ð°Ð½Ð½Ñ‹Ñ…: Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾ ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð² Ð´Ð»Ñ Ð·Ð°Ð³Ñ€ÑƒÐ·ÐºÐ¸")
        return 0

    start_timestamp_ms, end_timestamp_ms = _fetch_period(config, args.days, getattr(args, "end_timestamp_ms", None))
    include_open_interest = not _to_bool_flag(getattr(args, "skip_open_interest", False))
    failed_symbols: set[str] = set()
    fetch_summaries: dict[Timeframe, FetchSummary] = {}

    def _fetch_for_timeframe(
        requested_timeframe: Timeframe,
        symbols_to_fetch: list[str],
        *,
        emit_log: bool = True,
    ) -> None:
        logger.info(
            "Ð·Ð°Ð³Ñ€ÑƒÐ·ÐºÐ°-Ð´Ð°Ð½Ð½Ñ‹Ñ…: ÑÐ±Ð¾Ñ€ ÐºÑÑˆÐ° Ð´Ð»Ñ TF=%s (ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð²=%s)",
            requested_timeframe.value,
            len(symbols_to_fetch),
        )
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

    for timeframe in fetch_timeframes:
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
    fetch_timeframes = _resolve_fetch_timeframes(args, config.fetch.timeframes)

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
        "Ð¾Ð±Ð½Ð¾Ð²Ð»ÐµÐ½Ð¸Ðµ-ÐºÑÑˆÐ°: Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾ Ñ„ÑŒÑŽÑ‡ÐµÑ€ÑÐ¾Ð²=%s Ð¾Ñ‚Ð¿Ñ€Ð°Ð²Ð»ÐµÐ½Ð¾ Ð² fetch_all=%s",
        all_futures_count,
        len(symbols),
    )
    if not symbols:
        logger.info("Ð¾Ð±Ð½Ð¾Ð²Ð»ÐµÐ½Ð¸Ðµ-ÐºÑÑˆÐ°: Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾ ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð² Ð´Ð»Ñ Ð¾Ð±Ð½Ð¾Ð²Ð»ÐµÐ½Ð¸Ñ")
        return 0

    start_timestamp_ms, end_timestamp_ms = _fetch_period(config, args.days, getattr(args, "end_timestamp_ms", None))
    include_open_interest = not _to_bool_flag(getattr(args, "skip_open_interest", False))
    failed_symbols: set[str] = set()
    for index, timeframe in enumerate(fetch_timeframes):
        logger.info("Ð¾Ð±Ð½Ð¾Ð²Ð»ÐµÐ½Ð¸Ðµ-ÐºÑÑˆÐ°: ÑÐ±Ð¾Ñ€ ÐºÑÑˆÐ° Ð´Ð»Ñ TF=%s", timeframe.value)
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
        _log_fetch_summary(f"update-cache[{timeframe.value}]", logger, len(symbols), result.failed_symbols_count)
        failed_symbols.update(
            symbol
            for symbol in symbols
            if (symbol in result.ohlcv and not result.ohlcv[symbol].success)
            or (symbol in result.open_interest and not result.open_interest[symbol].success)
            or isinstance(result.market_caps.market_caps.get(symbol), str)
        )
        if index < len(fetch_timeframes) - 1:
            logger.info("Ð¾Ð±Ð½Ð¾Ð²Ð»ÐµÐ½Ð¸Ðµ-ÐºÑÑˆÐ°: cooldown after TF=%s sleep=75s", timeframe.value)
            time.sleep(75)

    exit_code = _fetch_exit_code(len(failed_symbols))
    _log_loaded_coins(logger, len(symbols), "updated")
    return exit_code


def _run_backtest_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-backtest", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    strategy_id = (
        _resolve_strategy_id(args)
        if getattr(args, "strategy", None) is not None
        else str(config.strategy.strategy_id).strip().lower()
    )
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
        if getattr(args, "bee_bite_deposit", None) is not None:
            config.strategy.bee_bite_deposit = float(args.bee_bite_deposit)
        if getattr(args, "bee_bite_risk_pct", None) is not None:
            config.strategy.bee_bite_risk_pct = float(args.bee_bite_risk_pct)
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
    elif strategy_id == "post_pump_absorption":
        config.strategy.post_pump_absorption_profile = parse_post_pump_absorption_profile_id(
            getattr(args, "ppa_profile", None),
            default=config.strategy.post_pump_absorption_profile,
        )
        if getattr(args, "ppa_deposit", None) is not None:
            config.strategy.post_pump_absorption_deposit = float(args.ppa_deposit)
        if getattr(args, "ppa_risk_pct", None) is not None:
            config.strategy.post_pump_absorption_risk_pct = float(args.ppa_risk_pct)
    levels_timeframe, entry_timeframe = _resolve_backtest_timeframes(
        strategy_id=strategy_id,
        args=args,
        configured_levels_timeframe=config.strategy.levels_timeframe,
        configured_entry_timeframe=config.strategy.entry_timeframe,
    )
    logger.info(
        "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: ÑÐ²Ð½Ñ‹Ð¹ Ð·Ð°Ð¿ÑƒÑÐº, ÑƒÑ€Ð¾Ð²Ð½Ð¸: %s, Ð²Ñ…Ð¾Ð´Ñ‹: %s",
        levels_timeframe.value,
        entry_timeframe.value,
    )

    preparer = DataPreparer(config.backtest.cache_dir)
    symbols = args.symbols or preparer.list_symbols(entry_timeframe)
    if not symbols:
        logger.info("Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÐµÐºÑ‚ÐµÑÑ‚Ð°: Ð½ÐµÑ‚ Ð´Ð°Ð½Ð½Ñ‹Ñ… Ð² ÐºÑÑˆÐµ")
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
        top_n_applied = top_n if pre_rank_enabled else "Ð½Ðµ Ð¿Ñ€Ð¸Ð¼ÐµÐ½ÑÐ»ÑÑ"
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
            top_preview_text = "Ð¿ÑƒÑÑ‚Ð¾"
        logger.info(
            "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: bee_bite stage1 symbols_total=%s passed=%s top_n=%s selected=%s reasons=%s",
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
                    "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: ÑÐ¸Ð¼Ð²Ð¾Ð» %s Ð¸ÑÐºÐ»ÑŽÑ‡Ñ‘Ð½ Ð¸Ð· pre-rank, Ð¿Ñ€Ð¸Ñ‡Ð¸Ð½Ð°=Ð¿ÑƒÑÑ‚Ð¾Ð¹ levels_tf=%s",
                    symbol,
                    levels_timeframe.value,
                )
                rejected_symbols_count += 1
                continue
            if "volume" not in levels_frame.columns:
                logger.info(
                    "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: ÑÐ¸Ð¼Ð²Ð¾Ð» %s Ð¸ÑÐºÐ»ÑŽÑ‡Ñ‘Ð½ Ð¸Ð· pre-rank, Ð¿Ñ€Ð¸Ñ‡Ð¸Ð½Ð°=Ð½ÐµÑ‚ ÐºÐ¾Ð»Ð¾Ð½ÐºÐ¸ volume Ð½Ð° levels_tf=%s",
                    symbol,
                    levels_timeframe.value,
                )
                rejected_symbols_count += 1
                continue

            volume_numeric = pd.Series(pd.to_numeric(levels_frame["volume"], errors="coerce"), index=levels_frame.index)
            volume_series = volume_numeric.dropna()
            if volume_series.empty:
                logger.debug(
                    "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: ÑÐ¸Ð¼Ð²Ð¾Ð» %s Ð¸ÑÐºÐ»ÑŽÑ‡Ñ‘Ð½ Ð¸Ð· pre-rank, Ð¿Ñ€Ð¸Ñ‡Ð¸Ð½Ð°=Ð½ÐµÑ‚ Ð²Ð°Ð»Ð¸Ð´Ð½Ð¾Ð³Ð¾ volume Ð½Ð° levels_tf=%s",
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
            top_preview_text = "Ð¿ÑƒÑÑ‚Ð¾"
    else:
        ranked_symbols_count = 0
        symbols = list(symbols)
        top_n_applied = "Ð½Ðµ Ð¿Ñ€Ð¸Ð¼ÐµÐ½ÑÐ»ÑÑ"
        top_preview_text = "pre-rank Ð¾Ñ‚ÐºÐ»ÑŽÑ‡Ñ‘Ð½"

    pre_rank_elapsed_seconds = time.perf_counter() - pre_rank_started_at
    logger.info(
        "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: pre-rank Ð²Ñ€ÐµÐ¼Ñ=%.3fs enabled=%s",
        pre_rank_elapsed_seconds,
        pre_filter_active,
    )
    if strategy_id == "bee_bite":
        logger.info(
            "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: bee_bite stage1 total=%s passed=%s rejected=%s top_n=%s selected=%s",
            symbols_before_ranking,
            ranked_symbols_count,
            rejected_symbols_count,
            top_n_applied,
            len(symbols),
        )
        if not pre_rank_enabled:
            logger.info(
                "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: bee_bite stage1 top_n Ð½Ðµ Ð·Ð°Ð´Ð°Ð½, Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐµÑ‚ÑÑ Ð²ÐµÑÑŒ stage1-Ð¾Ñ‚Ð±Ð¾Ñ€ (%s)",
                len(symbols),
            )
    else:
        logger.info(
            "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: pre-rank symbols_total=%s Ð²Ð°Ð»Ð¸Ð´Ð½Ñ‹Ð¹_volume_levels_tf=%s rejected=%s top_n=%s Ð²Ñ‹Ð±Ñ€Ð°Ð½Ð¾_Ð¿Ð¾ÑÐ»Ðµ_Ð¾Ñ‚ÑÐµÑ‡ÐµÐ½Ð¸Ñ=%s",
            symbols_before_ranking,
            ranked_symbols_count,
            rejected_symbols_count,
            top_n_applied,
            len(symbols),
        )
        if not pre_rank_enabled:
            logger.info(
                "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: pre-rank top_n Ð½Ðµ Ð·Ð°Ð´Ð°Ð½ Ð¸Ð»Ð¸ <= 0, Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐµÑ‚ÑÑ Ð¸ÑÑ…Ð¾Ð´Ð½Ñ‹Ð¹ ÑÐ¿Ð¸ÑÐ¾Ðº ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð² (%s)",
                len(symbols),
            )
    logger.info("Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: pre-rank top-list: %s", top_preview_text)

    if not symbols:
        if strategy_id == "bee_bite":
            logger.info("Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: Ñ€Ð°Ð½Ð½Ð¸Ð¹ Ð²Ñ‹Ñ…Ð¾Ð´, Ð¿Ð¾ÑÐ»Ðµ bee_bite stage1 ÑÐ¿Ð¸ÑÐ¾Ðº ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð² Ð¿ÑƒÑÑ‚")
        elif ranked_symbols_count == 0:
            logger.info(
                "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: Ñ€Ð°Ð½Ð½Ð¸Ð¹ Ð²Ñ‹Ñ…Ð¾Ð´, Ð½ÐµÑ‚ ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð² Ñ Ð²Ð°Ð»Ð¸Ð´Ð½Ñ‹Ð¼ Ð¾Ð±ÑŠÑ‘Ð¼Ð¾Ð¼ Ð½Ð° levels_tf=%s",
                levels_timeframe.value,
            )
        else:
            logger.info("Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: Ñ€Ð°Ð½Ð½Ð¸Ð¹ Ð²Ñ‹Ñ…Ð¾Ð´, Ð¿Ð¾ÑÐ»Ðµ Ð¿Ñ€Ð¸Ð¼ÐµÐ½ÐµÐ½Ð¸Ñ top_n=%s ÑÐ¿Ð¸ÑÐ¾Ðº ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð² Ð¿ÑƒÑÑ‚", top_n)
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
        entry_frame: pd.DataFrame | None = preloaded_entry_frames.get(symbol)
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
                "Ð°Ð½Ð°Ð»Ð¸Ð·-ÐºÑÑˆÐ°: Ð¿Ð¾Ð´Ð³Ð¾Ñ‚Ð¾Ð²ÐºÐ°-ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð² %s/%s (%.1f%%), eta=%ss",
                idx,
                symbols_total,
                progress,
                int(eta_seconds),
            )
    if not symbol_frames:
        logger.info("Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÐµÐºÑ‚ÐµÑÑ‚Ð°: Ð½Ðµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¿Ð¾Ð´Ð³Ð¾Ñ‚Ð¾Ð²Ð¸Ñ‚ÑŒ Ð´Ð°Ð½Ð½Ñ‹Ðµ")
        return 0

    strategy = build_strategy(config, logger)
    runner = BacktestRunner(
        config.backtest.results_dir,
        config.backtest.results_file_name,
        logger=logger,
    )
    symbols_used_ratio = symbols_used / symbols_total if symbols_total else 0.0
    logger.info(
        "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: ÑÐ²Ð¾Ð´ÐºÐ° Ð¿Ð¾ ÑÐ¸Ð¼Ð²Ð¾Ð»Ð°Ð¼ Ð²ÑÐµÐ³Ð¾=%s Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ð½Ð¾=%s Ð±ÐµÐ·_Ð´Ð°Ð½Ð½Ñ‹Ñ…_levels_tf=%s Ð±ÐµÐ·_Ð´Ð°Ð½Ð½Ñ‹Ñ…_entry_tf=%s",
        symbols_total,
        symbols_used,
        symbols_missing_levels_tf,
        symbols_missing_entry_tf,
    )
    if symbols_total and symbols_used_ratio < 0.2:
        logger.warning(
            "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐµÑ‚ÑÑ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ %.1f%% ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð² (%s Ð¸Ð· %s); Ñ€ÐµÐ·ÑƒÐ»ÑŒÑ‚Ð°Ñ‚ Ð±ÑÐºÑ‚ÐµÑÑ‚Ð° Ð¼Ð¾Ð¶ÐµÑ‚ Ð±Ñ‹Ñ‚ÑŒ Ð½ÐµÑ€ÐµÐ¿Ñ€ÐµÐ·ÐµÐ½Ñ‚Ð°Ñ‚Ð¸Ð²Ð½Ñ‹Ð¼",
            symbols_used_ratio * 100,
            symbols_used,
            symbols_total,
        )
    plot_from_results = _to_bool_flag(getattr(args, "plot_from_results", None), default=False)
    if plot_from_results:
        best_row = _load_plot_params_row_from_results(config, args, logger=logger, strategy_id=strategy_id)
        if best_row is None:
            return 1
        if not _plot_for_strategy_dispatch(
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
        "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: Ð²ÑÐµÐ³Ð¾=%s Ð¿Ñ€Ð¸Ð±Ñ‹Ð»ÑŒÐ½Ñ‹Ñ…=%s Ð»ÑƒÑ‡ÑˆÐ¸Ð¹_pf=%.4f ÐºÐ¾Ð¼Ð±Ð¸Ð½Ð°Ñ†Ð¸Ð¹_ÑÐ¾_ÑÐ´ÐµÐ»ÐºÐ°Ð¼Ð¸=%s ÑÑƒÐ¼Ð¼Ð°_ÑÐ´ÐµÐ»Ð¾Ðº_Ð¿Ð¾_ÑÐµÑ‚ÐºÐµ=%s ÑÑ€ÐµÐ´Ð½ÐµÐµ_ÑÐ´ÐµÐ»Ð¾Ðº_Ð½Ð°_ÐºÐ¾Ð¼Ð±Ð¸Ð½Ð°Ñ†Ð¸ÑŽ=%.4f Ð¼ÐµÐ´Ð¸Ð°Ð½Ð°_ÑÐ´ÐµÐ»Ð¾Ðº_Ð½Ð°_ÐºÐ¾Ð¼Ð±Ð¸Ð½Ð°Ñ†Ð¸ÑŽ=%.4f",
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
            "Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: Ð¾Ñ‚ÑÑƒÑ‚ÑÑ‚Ð²ÑƒÑŽÑ‚ ÑÐ´ÐµÐ»ÐºÐ¸ Ð¿Ð¾ Ð²ÑÐµÐ¼ ÐºÐ¾Ð¼Ð±Ð¸Ð½Ð°Ñ†Ð¸ÑÐ¼; Ð¿Ñ€Ð¾Ð²ÐµÑ€ÑŒÑ‚Ðµ Ð´Ð¾ÑÑ‚Ð°Ñ‚Ð¾Ñ‡Ð½Ð¾ÑÑ‚ÑŒ Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ð¸ Ð´Ð»Ñ levels_tf=%s Ð¸ ÑÐ¾Ð¾Ñ‚Ð²ÐµÑ‚ÑÑ‚Ð²Ð¸Ðµ Ñ‚Ð°Ð¹Ð¼Ñ„Ñ€ÐµÐ¹Ð¼Ð¾Ð² Ð² ÐºÑÑˆÐµ (%s/%s)",
            levels_timeframe.value,
            levels_timeframe.value,
            entry_timeframe.value,
        )

    should_plot = _to_bool_flag(getattr(args, "plot", None), default=False)
    if should_plot:
        if results.empty:
            logger.warning("Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: plot=true, Ð½Ð¾ Ñ€ÐµÐ·ÑƒÐ»ÑŒÑ‚Ð°Ñ‚Ñ‹ Ð¿ÑƒÑÑ‚Ñ‹Ðµ")
            return 0

        best_row = results.iloc[0]
        if not _plot_for_strategy_dispatch(
            config=config,
            args=args,
            logger=logger,
            strategy_id=strategy_id,
            strategy=strategy,
            symbol_frames=symbol_frames,
            params_row=best_row,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
            log_prefix="Ð·Ð°Ð¿ÑƒÑÐº-Ð±ÑÐºÑ‚ÐµÑÑ‚Ð°: plot=true",
        ):
            return 1
    return 0


def _run_ppa_research_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-ppa-research", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    timeframes = _resolve_ppa_research_timeframes(args)
    timestamp_label = time.strftime("%Y%m%d_%H%M%S")
    root_output_dir = (
        Path(args.output_dir)
        if getattr(args, "output_dir", None)
        else Path(config.backtest.results_dir) / "research" / "post_pump_absorption" / timestamp_label
    )
    root_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "run-ppa-research: timeframes=%s output_dir=%s",
        ",".join(timeframe.value for timeframe in timeframes),
        root_output_dir,
    )

    run_exit_codes: list[int] = []
    runs: list[PostPumpAbsorptionResearchRun] = []
    for timeframe in timeframes:
        scoped_config = deepcopy(config)
        scoped_config.strategy.strategy_id = "post_pump_absorption"
        scoped_config.backtest.results_dir = root_output_dir / timeframe.value
        scoped_args = _with_ppa_research_timeframe(args, timeframe)
        logger.info(
            "run-ppa-research: start timeframe=%s results_base=%s",
            timeframe.value,
            scoped_config.backtest.results_dir,
        )
        exit_code = _run_backtest_inner(scoped_config, scoped_args)
        run_exit_codes.append(exit_code)

        strategy_results_dir = Path(scoped_config.backtest.results_dir)
        runs.append(
            load_post_pump_absorption_research_run(
                timeframe=timeframe,
                strategy_results_dir=strategy_results_dir,
                results_file_name=scoped_config.backtest.results_file_name,
            )
        )
        logger.info(
            "run-ppa-research: finished timeframe=%s code=%s strategy_results_dir=%s",
            timeframe.value,
            exit_code,
            strategy_results_dir,
        )

    run_context = {
        "strategy": "post_pump_absorption",
        "timeframes": [timeframe.value for timeframe in timeframes],
        "symbols": list(getattr(args, "symbols", None) or []),
        "top_n": getattr(args, "top_n", None),
        "ppa_profile": getattr(args, "ppa_profile", None) or config.strategy.post_pump_absorption_profile,
        "ppa_deposit": getattr(args, "ppa_deposit", None) or config.strategy.post_pump_absorption_deposit,
        "ppa_risk_pct": getattr(args, "ppa_risk_pct", None) or config.strategy.post_pump_absorption_risk_pct,
        "generated_at_local": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    artifacts = build_post_pump_absorption_research_artifacts(
        output_dir=root_output_dir,
        runs=runs,
        run_context=run_context,
        logger=logger,
    )
    logger.info(
        "run-ppa-research: artifacts report=%s charts_dir=%s",
        artifacts["report"],
        artifacts["charts_dir"],
    )
    return max(run_exit_codes, default=0)


def _collect_oi_quality_issues(frame: pd.DataFrame) -> list[dict[str, str]]:
    if "open_interest" not in frame.columns:
        return [
            {
                "issue_type": QUALITY_OI_MISSING_COLUMN_ISSUE,
                "severity": QUALITY_SEVERITY_ERROR,
                "description": "ÐžÑ‚ÑÑƒÑ‚ÑÑ‚Ð²ÑƒÐµÑ‚ ÐºÐ¾Ð»Ð¾Ð½ÐºÐ° open_interest",
            }
        ]

    oi = pd.Series(pd.to_numeric(frame["open_interest"], errors="coerce"), index=frame.index)
    issues: list[dict[str, str]] = []

    if oi.isna().any():
        issues.append(
            {
                "issue_type": QUALITY_OI_MISSING_VALUES_ISSUE,
                "severity": QUALITY_SEVERITY_WARNING,
                "description": "Ð•ÑÑ‚ÑŒ ÑÑ‹Ñ€Ñ‹Ðµ Ð¿Ñ€Ð¾Ð¿ÑƒÑÐºÐ¸ open_interest",
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
                        "description": "ÐžÐ±Ð½Ð°Ñ€ÑƒÐ¶ÐµÐ½Ñ‹ Ð¿Ñ€Ð¾Ð¿ÑƒÑÐºÐ¸ open_interest Ð² Ð½Ð°Ñ‡Ð°Ð»Ðµ Ñ€ÑÐ´Ð°",
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
                    "description": "open_interest Ð¿Ð¾Ñ‡Ñ‚Ð¸ Ð½Ðµ Ð¼ÐµÐ½ÑÐµÑ‚ÑÑ Ð½Ð° ÑÑ‹Ñ€Ñ‹Ñ… Ð´Ð°Ð½Ð½Ñ‹Ñ…",
                }
            )

    return issues


def _build_quality_recommendations(summary: QualitySummary, symbols: dict[str, QualitySymbolStats]) -> list[str]:
    recommendations: list[str] = []
    if summary.gaps_total > 0:
        recommendations.append("Ð”Ð¾Ð·Ð°Ð³Ñ€ÑƒÐ·ÐºÐ° Ð´Ð¸Ð°Ð¿Ð°Ð·Ð¾Ð½Ð°: Ð·Ð°Ð¿ÑƒÑÑ‚Ð¸Ñ‚Ðµ update-cache Ð´Ð»Ñ ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð² Ñ Ð¿Ñ€Ð¾Ð¿ÑƒÑÐºÐ°Ð¼Ð¸")

    if any(data.gaps > 0 for data in symbols.values()):
        recommendations.append("ÐŸÑ€Ð¾Ð²ÐµÑ€ÐºÐ° Ñ‚Ð°Ð¹Ð¼Ñ„Ñ€ÐµÐ¹Ð¼Ð°: ÑƒÐ±ÐµÐ´Ð¸Ñ‚ÐµÑÑŒ, Ñ‡Ñ‚Ð¾ timeframe ÑÐ¾Ð²Ð¿Ð°Ð´Ð°ÐµÑ‚ Ñ ÐºÑÑˆÐµÐ¼")

    if summary.issues_total > 0:
        recommendations.append("Ð”ÐµÐ´ÑƒÐ¿Ð»Ð¸ÐºÐ°Ñ†Ð¸Ñ Ð¸ Ð¾Ñ‡Ð¸ÑÑ‚ÐºÐ°: Ð¿ÐµÑ€ÐµcÐ¾Ñ…Ñ€Ð°Ð½Ð¸Ñ‚Ðµ Ñ€ÑÐ´Ñ‹ Ñ ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸ÐµÐ¼ Ð´ÑƒÐ±Ð»ÐµÐ¹ Ð¸ Ð°Ð½Ð¾Ð¼Ð°Ð»Ð¸Ð¹")

    oi_problem_types = {
        QUALITY_OI_MISSING_COLUMN_ISSUE,
        QUALITY_OI_MISSING_VALUES_ISSUE,
        QUALITY_OI_LEADING_GAPS_ISSUE,
        QUALITY_OI_STALE_SERIES_ISSUE,
    }
    if any(problem in oi_problem_types for problem in summary.by_issue_type):
        recommendations.append("ÐŸÑ€Ð¾Ð²ÐµÑ€ÐºÐ° OI-Ð¸ÑÑ‚Ð¾Ñ‡Ð½Ð¸ÐºÐ°: Ð¿ÐµÑ€ÐµÐ·Ð°Ð¿ÑƒÑÑ‚Ð¸Ñ‚Ðµ Ð·Ð°Ð³Ñ€ÑƒÐ·ÐºÑƒ OI Ð¸ Ð¿Ñ€Ð¾Ð²ÐµÑ€ÑŒÑ‚Ðµ ÑÑ‹Ñ€Ñ‹Ðµ Ð¿Ñ€Ð¾Ð¿ÑƒÑÐºÐ¸/Ð°Ð½Ð¾Ð¼Ð°Ð»Ð¸Ð¸")

    if summary.issues_total > 0 or summary.gaps_total > 0:
        recommendations.append("ÐŸÐ¾Ð²Ñ‚Ð¾Ñ€Ð½Ð°Ñ Ð²Ð°Ð»Ð¸Ð´Ð°Ñ†Ð¸Ñ: Ð¿Ð¾ÑÐ»Ðµ Ð¸ÑÐ¿Ñ€Ð°Ð²Ð»ÐµÐ½Ð¸Ð¹ Ð²Ñ‹Ð¿Ð¾Ð»Ð½Ð¸Ñ‚Ðµ check-quality Ð¿Ð¾Ð²Ñ‚Ð¾Ñ€Ð½Ð¾")

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
        logger.info("Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ°-ÐºÐ°Ñ‡ÐµÑÑ‚Ð²Ð°: Ð½ÐµÑ‚ Ð´Ð°Ð½Ð½Ñ‹Ñ… Ð´Ð»Ñ Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ¸")
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
            logger.info(f"Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ°-ÐºÐ°Ñ‡ÐµÑÑ‚Ð²Ð°: {symbol} Ð¿Ñ€Ð¾Ð¿ÑƒÑ‰ÐµÐ½, Ð¿ÑƒÑÑ‚Ð¾Ð¹ Ð´Ð°Ñ‚Ð°ÑÐµÑ‚")
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
            f"Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ°-ÐºÐ°Ñ‡ÐµÑÑ‚Ð²Ð°: {symbol} Ð¿Ñ€Ð¾Ð±Ð»ÐµÐ¼Ñ‹={symbol_total_issues} Ð¿Ñ€Ð¾Ð¿ÑƒÑÐºÐ¸={len(gaps)} "
            f"Ð¿Ñ€Ð¾Ð±Ð»ÐµÐ¼Ñ‹_oi={len(oi_quality_issues)}"
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

    logger.info(f"Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ°-ÐºÐ°Ñ‡ÐµÑÑ‚Ð²Ð°: Ð¸Ñ‚Ð¾Ð³ Ð¿Ñ€Ð¾Ð±Ð»ÐµÐ¼Ñ‹={report.summary.issues_total} Ð¿Ñ€Ð¾Ð¿ÑƒÑÐºÐ¸={report.summary.gaps_total}")
    logger.info(f"Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ°-ÐºÐ°Ñ‡ÐµÑÑ‚Ð²Ð°: Ð¾Ñ‚Ñ‡ÐµÑ‚ ÑÐ¾Ñ…Ñ€Ð°Ð½ÐµÐ½ {output_path}")
    return 0


def _clear_cache_inner(config: AppConfig, args: argparse.Namespace) -> int:
    del args
    logger = get_logger("clear-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)

    cache_dir = config.backtest.cache_dir
    cache_dir_str = str(cache_dir).strip()
    if not cache_dir_str:
        logger.error("Ð¾Ñ‡Ð¸ÑÑ‚ÐºÐ°-ÐºÑÑˆÐ°: Ð¿ÑƒÑ‚ÑŒ Ðº Ð´Ð¸Ñ€ÐµÐºÑ‚Ð¾Ñ€Ð¸Ð¸ ÐºÑÑˆÐ° Ð¿ÑƒÑÑ‚Ð¾Ð¹, ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ðµ Ð¾Ñ‚Ð¼ÐµÐ½ÐµÐ½Ð¾")
        return 1

    resolved_cache_dir = cache_dir.expanduser().resolve()
    home_dir = Path.home().resolve()
    if resolved_cache_dir == Path(resolved_cache_dir.anchor):
        logger.error(f"Ð¾Ñ‡Ð¸ÑÑ‚ÐºÐ°-ÐºÑÑˆÐ°: Ð¿ÑƒÑ‚ÑŒ '{resolved_cache_dir}' ÑƒÐºÐ°Ð·Ñ‹Ð²Ð°ÐµÑ‚ Ð½Ð° ÐºÐ¾Ñ€ÐµÐ½ÑŒ Ð¤Ð¡, ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ðµ Ð¾Ñ‚Ð¼ÐµÐ½ÐµÐ½Ð¾")
        return 1

    if resolved_cache_dir == home_dir:
        logger.error(f"Ð¾Ñ‡Ð¸ÑÑ‚ÐºÐ°-ÐºÑÑˆÐ°: Ð¿ÑƒÑ‚ÑŒ '{resolved_cache_dir}' ÑƒÐºÐ°Ð·Ñ‹Ð²Ð°ÐµÑ‚ Ð½Ð° Ð´Ð¾Ð¼Ð°ÑˆÐ½ÑŽÑŽ Ð´Ð¸Ñ€ÐµÐºÑ‚Ð¾Ñ€Ð¸ÑŽ, ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ðµ Ð¾Ñ‚Ð¼ÐµÐ½ÐµÐ½Ð¾")
        return 1

    logger.info(f"Ð¾Ñ‡Ð¸ÑÑ‚ÐºÐ°-ÐºÑÑˆÐ°: ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ðµ ÑÐ¾Ð´ÐµÑ€Ð¶Ð¸Ð¼Ð¾Ð³Ð¾ {resolved_cache_dir}")
    shutil.rmtree(resolved_cache_dir, ignore_errors=True)
    resolved_cache_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Ð¾Ñ‡Ð¸ÑÑ‚ÐºÐ°-ÐºÑÑˆÐ°: Ð´Ð¸Ñ€ÐµÐºÑ‚Ð¾Ñ€Ð¸Ñ Ð¿ÐµÑ€ÐµÑÐ¾Ð·Ð´Ð°Ð½Ð° {resolved_cache_dir}")
    return 0



# endregion ÐŸÑ€Ð¸Ð²Ð°Ñ‚Ð½Ñ‹Ðµ

# ÐŸÑƒÐ±Ð»Ð¸Ñ‡Ð½Ñ‹Ðµ Ñ‚Ð¾Ñ‡ÐºÐ¸ Ð²Ñ…Ð¾Ð´Ð°

def fetch_data(config: AppConfig, args: argparse.Namespace) -> int:
    """Ð—Ð°Ð¿ÑƒÑÐºÐ°ÐµÑ‚ ÑÑ†ÐµÐ½Ð°Ñ€Ð¸Ð¹ Ð·Ð°Ð³Ñ€ÑƒÐ·ÐºÐ¸ Ñ€Ñ‹Ð½Ð¾Ñ‡Ð½Ñ‹Ñ… Ð´Ð°Ð½Ð½Ñ‹Ñ…."""
    return _run_with_logging("fetch-data", config, lambda: _fetch_data_inner(config, args))


def update_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """ÐžÐ±Ð½Ð¾Ð²Ð»ÑÐµÑ‚ Ð»Ð¾ÐºÐ°Ð»ÑŒÐ½Ñ‹Ð¹ ÐºÑÑˆ Ð´Ð°Ð½Ð½Ñ‹Ñ…."""
    return _run_with_logging("update-cache", config, lambda: _update_cache_inner(config, args))


def run_backtest(config: AppConfig, args: argparse.Namespace) -> int:
    """Ð—Ð°Ð¿ÑƒÑÐºÐ°ÐµÑ‚ Ð±ÑÐºÑ‚ÐµÑÑ‚ Ð¿Ð¾ Ñ‚ÐµÐºÑƒÑ‰ÐµÐ¹ ÐºÐ¾Ð½Ñ„Ð¸Ð³ÑƒÑ€Ð°Ñ†Ð¸Ð¸."""
    return _run_with_logging("run-backtest", config, lambda: _run_backtest_inner(config, args))


def run_ppa_research(config: AppConfig, args: argparse.Namespace) -> int:
    """Runs post_pump_absorption on multiple micro timeframes and builds research artifacts."""
    return _run_with_logging("run-ppa-research", config, lambda: _run_ppa_research_inner(config, args))


def check_quality(config: AppConfig, args: argparse.Namespace) -> int:
    """ÐŸÑ€Ð¾Ð²ÐµÑ€ÑÐµÑ‚ ÐºÐ°Ñ‡ÐµÑÑ‚Ð²Ð¾ Ð¸ Ñ†ÐµÐ»Ð¾ÑÑ‚Ð½Ð¾ÑÑ‚ÑŒ Ð´Ð°Ð½Ð½Ñ‹Ñ…."""
    return _run_with_logging("check-quality", config, lambda: _check_quality_inner(config, args))


def clear_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """ÐžÑ‡Ð¸Ñ‰Ð°ÐµÑ‚ Ð´Ð¸Ñ€ÐµÐºÑ‚Ð¾Ñ€Ð¸ÑŽ Ð»Ð¾ÐºÐ°Ð»ÑŒÐ½Ð¾Ð³Ð¾ ÐºÑÑˆÐ° Ð¸ Ð¿ÐµÑ€ÐµÑÐ¾Ð·Ð´Ð°Ñ‘Ñ‚ ÐµÑ‘."""
    return _run_with_logging("clear-cache", config, lambda: _clear_cache_inner(config, args))




