"""Модуль проекта."""

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
from dataclasses import asdict, dataclass, replace
from logging import Logger
from pathlib import Path
from typing import Callable, cast

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.patches import Rectangle
from matplotlib.ticker import LinearLocator
from matplotlib.transforms import blended_transform_factory

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
from strategy.hourly_asia_pump import (
    DEFAULT_ASIA_END_HOUR_UTC,
    DEFAULT_ASIA_START_HOUR_UTC,
    DEFAULT_MAX_FOLLOW_MINUTES,
    DEFAULT_TRIGGER_MINUTE,
    HOURLY_ASIA_PUMP_SUPPORTED_TIMEFRAMES,
    build_hourly_asia_pump_production_artifacts,
    build_hourly_asia_pump_research_artifacts,
    build_hourly_asia_pump_session_short_edge_artifacts,
    build_hourly_asia_pump_short_edge_artifacts,
    build_hourly_asia_pump_static_combo_artifacts,
    build_hourly_asia_pump_unified_edge_artifacts,
    build_hourly_asia_pump_unified_artifacts,
    parse_hourly_asia_pump_profile_id,
)
from strategy.post_pump_absorption import (
    PostPumpAbsorptionParams,
    PostPumpAbsorptionStrategy,
    parse_post_pump_absorption_profile_id,
)
from strategy.post_pump_absorption.config import (
    POST_PUMP_ABSORPTION_DEFAULT_TIMEFRAME,
    POST_PUMP_ABSORPTION_OI_SOURCE_TIMEFRAME,
    POST_PUMP_ABSORPTION_SUPPORTED_ENTRY_TIMEFRAMES,
)
from strategy.post_pump_absorption.engine import PPA_STAGE_SEQUENCE
from strategy.post_pump_absorption.research import (
    PostPumpAbsorptionResearchRun,
    build_post_pump_absorption_research_artifacts,
    load_post_pump_absorption_research_run,
)
from strategy.pno import PnoParams, PnoStrategy
from strategy.pno.config import (
    PNO_BACKTEST_TIMEFRAME_PAIRS,
    resolve_pno_default_timeframe_pair,
    validate_pno_timeframe_pair,
)
from strategy.pno.engine import (
    PNO_STAGE_4_LEVEL,
    PNO_STAGE_5_TRADE,
    PNO_STAGE_SEQUENCE,
)
from utils.logger import get_logger
from utils.symbols import normalize_symbol
from vectorbt_runner import BacktestRunner, DataPreparer, SymbolMtfFrames

# region Приватные

_PROGRESS_LOG_EVERY = 100
_PPA_STAGE_PRESETS: dict[str, tuple[int | None, int | None]] = {
    **{f"s{idx}": (idx, None) for idx in range(1, len(PPA_STAGE_SEQUENCE) + 1)},
    **{f"stage{idx}": (idx, None) for idx in range(1, len(PPA_STAGE_SEQUENCE) + 1)},
    **{f"t{idx}": (None, idx) for idx in range(1, len(PPA_STAGE_SEQUENCE) + 1)},
    **{f"through{idx}": (None, idx) for idx in range(1, len(PPA_STAGE_SEQUENCE) + 1)},
}
_PNO_STAGE_PRESETS: dict[str, tuple[int | None, int | None]] = {
    **{f"s{idx}": (idx, None) for idx in range(1, len(PNO_STAGE_SEQUENCE) + 1)},
    **{f"stage{idx}": (idx, None) for idx in range(1, len(PNO_STAGE_SEQUENCE) + 1)},
    **{f"t{idx}": (None, idx) for idx in range(1, len(PNO_STAGE_SEQUENCE) + 1)},
    **{f"through{idx}": (None, idx) for idx in range(1, len(PNO_STAGE_SEQUENCE) + 1)},
}


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
        if normalized not in {"bee_bite", "post_pump_absorption", "pno"}:
            raise ValueError(f"Неподдерживаемый strategy_id: {normalized}")
        return normalized
    return "bee_bite"


def _resolve_results_dir_for_strategy(base_results_dir: Path, strategy_id: str) -> Path:
    if strategy_id not in {"bee_bite", "post_pump_absorption", "pno"}:
        raise ValueError(f"Неподдерживаемый strategy_id: {strategy_id}")
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
        oi_min_delta_pct=float(row.get("ppa_oi_min_delta_pct", 0.0)),
        oi_ratio_threshold_relaxation=float(row.get("ppa_oi_ratio_threshold_relaxation", 0.01)),
        oi_volume_mult_relaxation=float(row.get("ppa_oi_volume_mult_relaxation", 0.05)),
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


def _build_pno_params_template_from_row(
    row: pd.Series,
    *,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
) -> PnoParams:
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

    def _optional_int(column_name: str) -> int | None:
        raw_value = row.get(column_name)
        return None if _is_missing_scalar(raw_value) else int(raw_value)

    def _float_or_default(column_name: str, default: float) -> float:
        resolved = _optional_float(column_name)
        return default if resolved is None else resolved

    def _int_or_default(column_name: str, default: int) -> int:
        resolved = _optional_int(column_name)
        return default if resolved is None else resolved

    def _str_or_default(column_name: str, default: str) -> str:
        raw_value = row.get(column_name)
        return default if _is_missing_scalar(raw_value) else str(raw_value)

    defaults = PnoParams(
        symbol="",
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
    )
    deposit = _optional_float("pno_deposit") or defaults.pno_deposit
    risk_pct = _optional_float("pno_risk_pct")
    r_trade = _optional_float("pno_r_trade")
    if risk_pct is None:
        risk_pct = (r_trade / deposit) if r_trade is not None else defaults.pno_risk_pct
    resolved_trade_risk = r_trade if r_trade is not None else deposit * risk_pct

    return PnoParams(
        symbol="",
        pno_variant_id=str(row.get("pno_variant_id", defaults.pno_variant_id)),
        entry_confirmation_mode=_str_or_default(
            "pno_entry_confirmation_mode",
            defaults.entry_confirmation_mode,
        ),
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        pno_deposit=deposit,
        pno_risk_pct=risk_pct,
        pno_r_trade=resolved_trade_risk,
        fee_rate=_float_or_default("pno_fee_rate", defaults.fee_rate),
        min_data_5m=_int_or_default("pno_min_data_5m", defaults.min_data_5m),
        min_data_1m=_int_or_default("pno_min_data_1m", defaults.min_data_1m),
        min_stage1_leg_v1=_float_or_default("pno_min_stage1_leg_v1", defaults.min_stage1_leg_v1),
        min_stage1_leg_v5_fraction=_float_or_default("pno_min_stage1_leg_v5_fraction", defaults.min_stage1_leg_v5_fraction),
        stage1_hold_fraction=_float_or_default("pno_stage1_hold_fraction", defaults.stage1_hold_fraction),
        pullback_min_v1=_float_or_default("pno_pullback_min_v1", defaults.pullback_min_v1),
        pullback_min_pump_fraction_5m=_float_or_default(
            "pno_pullback_min_pump_fraction_5m",
            defaults.pullback_min_pump_fraction_5m,
        ),
        pullback_valid_max_leg_fraction=_float_or_default("pno_pullback_valid_max_leg_fraction", defaults.pullback_valid_max_leg_fraction),
        pullback_invalid_max_leg_fraction=_float_or_default("pno_pullback_invalid_max_leg_fraction", defaults.pullback_invalid_max_leg_fraction),
        pullback_valid_max_v5=_float_or_default("pno_pullback_valid_max_v5", defaults.pullback_valid_max_v5),
        pullback_invalid_max_v5=_float_or_default("pno_pullback_invalid_max_v5", defaults.pullback_invalid_max_v5),
        pullback_max_age_bars=_int_or_default("pno_pullback_max_age_bars", defaults.pullback_max_age_bars),
        stage1_min_cumulative_quote_volume=_float_or_default(
            "pno_stage1_min_cumulative_quote_volume",
            defaults.stage1_min_cumulative_quote_volume,
        ),
        stage1_pre_pump_ema_crosses_min=_int_or_default(
            "pno_stage1_pre_pump_ema_crosses_min",
            defaults.stage1_pre_pump_ema_crosses_min,
        ),
        stage1_barcode_max_fraction_1h=_float_or_default(
            "pno_stage1_barcode_max_fraction_1h",
            defaults.stage1_barcode_max_fraction_1h,
        ),
        stage1_barcode_tr_atr_fraction=_float_or_default(
            "pno_stage1_barcode_tr_atr_fraction",
            defaults.stage1_barcode_tr_atr_fraction,
        ),
        stage1_barcode_tr_price_fraction=_float_or_default(
            "pno_stage1_barcode_tr_price_fraction",
            defaults.stage1_barcode_tr_price_fraction,
        ),
        stage1_min_impulse_atr_pre=_float_or_default("pno_stage1_min_impulse_atr_pre", defaults.stage1_min_impulse_atr_pre),
        stage1_min_peak_bar_tr_atr_pre=_float_or_default(
            "pno_stage1_min_peak_bar_tr_atr_pre",
            defaults.stage1_min_peak_bar_tr_atr_pre,
        ),
        stage1_min_volume_ratio_start=_float_or_default("pno_stage1_min_volume_ratio_start", defaults.stage1_min_volume_ratio_start),
        stage1_min_volume_ratio_continue=_float_or_default(
            "pno_stage1_min_volume_ratio_continue",
            defaults.stage1_min_volume_ratio_continue,
        ),
        stage1_min_path_efficiency=_float_or_default("pno_stage1_min_path_efficiency", defaults.stage1_min_path_efficiency),
        stage1_max_wick_share=_float_or_default("pno_stage1_max_wick_share", defaults.stage1_max_wick_share),
        stage1_min_pump_pct=_float_or_default("pno_stage1_min_pump_pct", defaults.stage1_min_pump_pct),
        stage1_min_pretrend_range_ratio_2h=_float_or_default(
            "pno_stage1_min_pretrend_range_ratio_2h",
            defaults.stage1_min_pretrend_range_ratio_2h,
        ),
        stage1_pre_pump_high_max_fraction_of_leg=_float_or_default(
            "pno_stage1_pre_pump_high_max_fraction_of_leg",
            defaults.stage1_pre_pump_high_max_fraction_of_leg,
        ),
        level_cluster_spread_v1=_float_or_default("pno_level_cluster_spread_v1", defaults.level_cluster_spread_v1),
        level_cluster_relaxed_spread_v1=_float_or_default(
            "pno_level_cluster_relaxed_spread_v1",
            defaults.level_cluster_relaxed_spread_v1,
        ),
        level_latest_high_max_age_bars=_int_or_default(
            "pno_level_latest_high_max_age_bars",
            defaults.level_latest_high_max_age_bars,
        ),
        level_touch_tolerance_v1=_float_or_default("pno_level_touch_tolerance_v1", defaults.level_touch_tolerance_v1),
        level_low_minor_break_v1=_float_or_default("pno_level_low_minor_break_v1", defaults.level_low_minor_break_v1),
        level_low_major_break_v1=_float_or_default("pno_level_low_major_break_v1", defaults.level_low_major_break_v1),
        level_min_maturity_fraction=_float_or_default(
            "pno_level_min_maturity_fraction",
            defaults.level_min_maturity_fraction,
        ),
        level_rearm_min_distance_v1=_float_or_default(
            "pno_level_rearm_min_distance_v1",
            defaults.level_rearm_min_distance_v1,
        ),
        max_level_touches=_int_or_default("pno_max_level_touches", defaults.max_level_touches),
        min_score=_float_or_default("pno_min_score", defaults.min_score),
        strong_score=_float_or_default("pno_strong_score", defaults.strong_score),
        slip_plan_v1_fraction=_float_or_default("pno_slip_plan_v1_fraction", defaults.slip_plan_v1_fraction),
        min_tick_fraction=_float_or_default("pno_min_tick_fraction", defaults.min_tick_fraction),
        max_entry_pullback_fraction=_float_or_default("pno_max_entry_pullback_fraction", defaults.max_entry_pullback_fraction),
    )


def _build_pno_params_from_row(
    row: pd.Series,
    *,
    symbol: str,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
) -> PnoParams:
    template = _build_pno_params_template_from_row(
        row,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
    )
    return replace(template, symbol=symbol)


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


def _resolve_ppa_stage_ids(args: argparse.Namespace) -> tuple[str, ...]:
    raw_stage = getattr(args, "ppa_stage", None)
    raw_through_stage = getattr(args, "ppa_through_stage", None)
    if raw_stage is not None and raw_through_stage is not None:
        raise ValueError("Use only one of --ppa-stage or --ppa-through-stage")

    if raw_stage is not None:
        stage_number = int(raw_stage)
        if stage_number < 1 or stage_number > len(PPA_STAGE_SEQUENCE):
            raise ValueError(f"--ppa-stage must be in range 1..{len(PPA_STAGE_SEQUENCE)}")
        return (PPA_STAGE_SEQUENCE[stage_number - 1],)

    if raw_through_stage is not None:
        stage_number = int(raw_through_stage)
        if stage_number < 1 or stage_number > len(PPA_STAGE_SEQUENCE):
            raise ValueError(f"--ppa-through-stage must be in range 1..{len(PPA_STAGE_SEQUENCE)}")
        return tuple(PPA_STAGE_SEQUENCE[:stage_number])

    return tuple(PPA_STAGE_SEQUENCE)


def _resolve_pno_stage_ids(args: argparse.Namespace) -> tuple[str, ...]:
    raw_stage = getattr(args, "pno_stage", None)
    raw_through_stage = getattr(args, "pno_through_stage", None)
    if raw_stage is not None and raw_through_stage is not None:
        raise ValueError("Use only one of --pno-stage or --pno-through-stage")

    if raw_stage is not None:
        stage_number = int(raw_stage)
        if stage_number < 1 or stage_number > len(PNO_STAGE_SEQUENCE):
            raise ValueError(f"--pno-stage must be in range 1..{len(PNO_STAGE_SEQUENCE)}")
        return (PNO_STAGE_SEQUENCE[stage_number - 1],)

    if raw_through_stage is not None:
        stage_number = int(raw_through_stage)
        if stage_number < 1 or stage_number > len(PNO_STAGE_SEQUENCE):
            raise ValueError(f"--pno-through-stage must be in range 1..{len(PNO_STAGE_SEQUENCE)}")
        return tuple(PNO_STAGE_SEQUENCE[:stage_number])

    return tuple(PNO_STAGE_SEQUENCE)


def _resolve_ppa_stage_preset(raw_value: object) -> tuple[int | None, int | None, str]:
    preset = str(raw_value or "").strip().lower()
    if preset not in _PPA_STAGE_PRESETS:
        supported = ", ".join(sorted(_PPA_STAGE_PRESETS))
        raise ValueError(f"Unsupported ppa-stage preset: {preset}. Supported: {supported}")
    stage, through_stage = _PPA_STAGE_PRESETS[preset]
    return stage, through_stage, preset


def _resolve_pno_stage_preset(raw_value: object) -> tuple[int | None, int | None, str]:
    preset = str(raw_value or "").strip().lower()
    if preset not in _PNO_STAGE_PRESETS:
        supported = ", ".join(sorted(_PNO_STAGE_PRESETS))
        raise ValueError(f"Unsupported pno-stage preset: {preset}. Supported: {supported}")
    stage, through_stage = _PNO_STAGE_PRESETS[preset]
    return stage, through_stage, preset


def _ppa_stage_metric_column_name(stage_id: str) -> str:
    return f"ppa_stage_hits_{stage_id}"


def _export_ppa_stage_reviews(
    *,
    diagnostics_dir: Path,
    stage_rows_by_stage: dict[str, list[dict[str, object]]],
    selected_stage_ids: tuple[str, ...],
) -> None:
    stage_reviews_dir = diagnostics_dir / "stage_reviews"
    stage_reviews_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, object]] = []
    for stage_id in selected_stage_ids:
        rows = stage_rows_by_stage.get(stage_id, [])
        stage_dir = stage_reviews_dir / stage_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        events_frame = pd.DataFrame(rows)
        events_path = stage_dir / "events.csv"
        events_frame.to_csv(events_path, index=False)

        summary_rows: list[dict[str, object]] = []
        if not events_frame.empty and "symbol" in events_frame.columns:
            for symbol, group in events_frame.groupby("symbol", sort=True):
                summary_rows.append(
                    {
                        "symbol": symbol,
                        "events_count": int(len(group)),
                        "first_timestamp_ms": int(pd.to_numeric(group["timestamp_ms"], errors="coerce").dropna().min())
                        if "timestamp_ms" in group.columns and not group.empty
                        else None,
                        "last_timestamp_ms": int(pd.to_numeric(group["timestamp_ms"], errors="coerce").dropna().max())
                        if "timestamp_ms" in group.columns and not group.empty
                        else None,
                    }
                )
        pd.DataFrame(summary_rows).to_csv(stage_dir / "summary_by_symbol.csv", index=False)
        manifest_rows.append(
            {
                "stage_id": stage_id,
                "events_count": int(len(rows)),
                "events_path": str(events_path),
                "summary_path": str(stage_dir / "summary_by_symbol.csv"),
            }
        )

    pd.DataFrame(manifest_rows).to_csv(stage_reviews_dir / "manifest.csv", index=False)


def _read_csv_or_empty(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def _safe_int(value: object) -> int | None:
    parsed = _safe_float(value)
    return None if parsed is None else int(parsed)


def _sanitize_plot_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value)


def _to_compact_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _format_eta_compact(seconds: float | None) -> str:
    if seconds is None or not np.isfinite(seconds) or seconds < 0.0:
        return "n/a"
    total_seconds = int(round(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _build_pno_plot_frame(
    *,
    levels_frame: pd.DataFrame,
    entry_frame: pd.DataFrame,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> pd.DataFrame:
    selected_columns = ["timestamp", "open", "high", "low", "close", "volume"]
    if {"ema9", "ema20"}.issubset(entry_frame.columns):
        selected_columns.extend(["ema9", "ema20"])
    plot_frame = entry_frame.loc[
        (entry_frame["timestamp"] >= start_timestamp_ms) & (entry_frame["timestamp"] <= end_timestamp_ms),
        selected_columns,
    ].copy()
    if plot_frame.empty:
        return plot_frame
    if {"ema9", "ema20"}.issubset(plot_frame.columns):
        return plot_frame

    levels_ema = _prepare_pno_levels_ema_source(levels_frame)
    if levels_ema.empty:
        plot_frame["ema9"] = np.nan
        plot_frame["ema20"] = np.nan
        return plot_frame

    levels_timestamps = levels_ema["timestamp"].to_numpy(dtype=np.float64)
    plot_timestamps = plot_frame["timestamp"].to_numpy(dtype=np.float64)
    plot_frame["ema9"] = np.interp(plot_timestamps, levels_timestamps, levels_ema["ema9"].to_numpy(dtype=np.float64))
    plot_frame["ema20"] = np.interp(plot_timestamps, levels_timestamps, levels_ema["ema20"].to_numpy(dtype=np.float64))
    return plot_frame


def _prepare_pno_levels_ema_source(levels_frame: pd.DataFrame) -> pd.DataFrame:
    if {"timestamp", "ema9", "ema20"}.issubset(levels_frame.columns):
        levels_ema = levels_frame.loc[:, ["timestamp", "ema9", "ema20"]].dropna(subset=["timestamp", "ema9", "ema20"])
    else:
        levels_ema = levels_frame.loc[:, ["timestamp", "close"]].dropna(subset=["timestamp", "close"]).copy()
        if levels_ema.empty:
            return levels_ema
        levels_ema["ema9"] = levels_ema["close"].ewm(span=9, adjust=False).mean()
        levels_ema["ema20"] = levels_ema["close"].ewm(span=20, adjust=False).mean()

    if levels_ema.empty:
        return levels_ema
    levels_timestamps = levels_ema["timestamp"].to_numpy(dtype=np.float64, copy=False)
    if levels_timestamps.size > 1 and np.any(levels_timestamps[1:] < levels_timestamps[:-1]):
        levels_ema = levels_ema.iloc[np.argsort(levels_timestamps, kind="stable")]
        levels_timestamps = levels_ema["timestamp"].to_numpy(dtype=np.float64, copy=False)
    if levels_timestamps.size > 1:
        unique_mask = np.ones(len(levels_ema), dtype=bool)
        unique_mask[:-1] = levels_timestamps[:-1] != levels_timestamps[1:]
        if not bool(unique_mask.all()):
            levels_ema = levels_ema.loc[unique_mask]
    return levels_ema


def _prepare_pno_levels_plot_source(levels_frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        column
        for column in (
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "ema9",
            "ema20",
            "open_interest",
            "number_of_trades",
            "trades",
            "trade_count",
            "taker_buy_volume",
            "taker_buy_quote_volume",
        )
        if column in levels_frame.columns
    ]
    prepared = levels_frame.loc[:, columns].copy()
    if prepared.empty:
        return prepared
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    numeric_columns = [
        column
        for column in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "ema9",
            "ema20",
            "open_interest",
            "number_of_trades",
            "trades",
            "trade_count",
            "taker_buy_volume",
            "taker_buy_quote_volume",
        )
        if column in prepared.columns
    ]
    for column in numeric_columns:
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    if prepared.empty:
        return prepared
    timestamps = prepared["timestamp"].to_numpy(dtype=np.float64, copy=False)
    if timestamps.size > 1 and np.any(timestamps[1:] < timestamps[:-1]):
        prepared = prepared.iloc[np.argsort(timestamps, kind="stable")]
        timestamps = prepared["timestamp"].to_numpy(dtype=np.float64, copy=False)
    if timestamps.size > 1:
        unique_mask = np.ones(len(prepared), dtype=bool)
        unique_mask[:-1] = timestamps[:-1] != timestamps[1:]
        if not bool(unique_mask.all()):
            prepared = prepared.loc[unique_mask]
    if "ema9" not in prepared.columns or "ema20" not in prepared.columns:
        prepared["ema9"] = prepared["close"].ewm(span=9, adjust=False).mean()
        prepared["ema20"] = prepared["close"].ewm(span=20, adjust=False).mean()
    return prepared


def _prepare_pno_entry_plot_source(entry_frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        column
        for column in (
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "open_interest",
            "number_of_trades",
            "trades",
            "trade_count",
            "taker_buy_volume",
            "taker_buy_quote_volume",
        )
        if column in entry_frame.columns
    ]
    prepared = entry_frame.loc[:, columns].copy()
    if prepared.empty:
        return prepared
    prepared["timestamp"] = pd.to_numeric(prepared["timestamp"], errors="coerce")
    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
        "number_of_trades",
        "trades",
        "trade_count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
    ):
        if column not in prepared.columns:
            continue
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    if prepared.empty:
        return prepared
    timestamps = prepared["timestamp"].to_numpy(dtype=np.float64, copy=False)
    if timestamps.size > 1 and np.any(timestamps[1:] < timestamps[:-1]):
        prepared = prepared.iloc[np.argsort(timestamps, kind="stable")]
        timestamps = prepared["timestamp"].to_numpy(dtype=np.float64, copy=False)
    if timestamps.size > 1:
        unique_mask = np.ones(len(prepared), dtype=bool)
        unique_mask[:-1] = timestamps[:-1] != timestamps[1:]
        if not bool(unique_mask.all()):
            prepared = prepared.loc[unique_mask]
    return prepared


def _prepare_pno_entry_plot_source_with_ema(*, levels_frame: pd.DataFrame, entry_frame: pd.DataFrame) -> pd.DataFrame:
    prepared = _prepare_pno_entry_plot_source(entry_frame)
    if prepared.empty or {"ema9", "ema20"}.issubset(prepared.columns):
        return prepared
    levels_ema = _prepare_pno_levels_ema_source(levels_frame)
    if levels_ema.empty:
        prepared["ema9"] = np.nan
        prepared["ema20"] = np.nan
        return prepared
    entry_timestamps = prepared["timestamp"].to_numpy(dtype=np.float64, copy=False)
    levels_timestamps = levels_ema["timestamp"].to_numpy(dtype=np.float64, copy=False)
    prepared["ema9"] = np.interp(entry_timestamps, levels_timestamps, levels_ema["ema9"].to_numpy(dtype=np.float64))
    prepared["ema20"] = np.interp(entry_timestamps, levels_timestamps, levels_ema["ema20"].to_numpy(dtype=np.float64))
    return prepared


_PNO_PLOT_FIGURE_FACE = "#08111f"
_PNO_PLOT_AXIS_FACE = "#0f172a"
_PNO_PLOT_GRID = "#334155"
_PNO_PLOT_TEXT = "#dbe4f0"
_PNO_PLOT_MUTED = "#94a3b8"
_PNO_PLOT_UP = "#22c55e"
_PNO_PLOT_DOWN = "#f97316"
_PNO_PLOT_EMA9 = "#f59e0b"
_PNO_PLOT_EMA20 = "#38bdf8"
_PNO_PLOT_PUMP = "#a3e635"
_PNO_PLOT_LEVEL = "#c084fc"
_PNO_PLOT_ENTRY = "#f8fafc"
_PNO_PLOT_EXIT = "#fde047"
_PNO_PLOT_RISK_FACE = "#7f1d1d"
_PNO_PLOT_RISK_EDGE = "#ef4444"
_PNO_PLOT_PROFIT_FACE = "#14532d"
_PNO_PLOT_PROFIT_EDGE = "#22c55e"
_PNO_PLOT_PANEL_EDGE = "#1e293b"
_PNO_PLOT_CANDLE_WIDTH = 0.64
_PNO_PLOT_5M_CANDLE_WIDTH = 4.0
_PNO_PLOT_MAX_X_TICKS = 8
_PNO_TRADE_CHART_FIGSIZE = (8.0, 8.0)
_PNO_TRADE_CHART_HEIGHT_RATIOS = [4, 2, 1]
_PNO_TRADE_SLEEP_LOOKBACK_BARS = 12
_PNO_PLOT_AXIS_TAG_LABEL_WIDTH = 7
_PNO_PLOT_AXIS_TAG_TEXT_WIDTH = 20


def _draw_pno_candles(ax: plt.Axes, frame: pd.DataFrame, x_values: np.ndarray) -> None:
    opens = frame["open"].to_numpy(dtype=np.float64)
    highs = frame["high"].to_numpy(dtype=np.float64)
    lows = frame["low"].to_numpy(dtype=np.float64)
    closes = frame["close"].to_numpy(dtype=np.float64)
    up_mask = closes >= opens
    wick_segments = np.stack(
        [
            np.column_stack([x_values, lows]),
            np.column_stack([x_values, highs]),
        ],
        axis=1,
    )
    wick_colors = np.where(up_mask, _PNO_PLOT_UP, _PNO_PLOT_DOWN)
    ax.add_collection(
        LineCollection(
            wick_segments,
            colors=wick_colors.tolist(),
            linewidths=1.0,
            alpha=1.0,
            zorder=3,
        )
    )
    body_lows = np.minimum(opens, closes)
    body_heights = np.maximum(np.abs(closes - opens), 1e-9)
    body_patches = [
        Rectangle(
            (float(x_pos) - _PNO_PLOT_CANDLE_WIDTH / 2.0, float(body_low)),
            _PNO_PLOT_CANDLE_WIDTH,
            float(body_height),
        )
        for x_pos, body_low, body_height in zip(x_values, body_lows, body_heights, strict=False)
    ]
    ax.add_collection(
        PatchCollection(
            body_patches,
            facecolor=wick_colors.tolist(),
            edgecolor=wick_colors.tolist(),
            linewidth=0.8,
            alpha=1.0,
            zorder=4,
            match_original=False,
        )
    )


def _draw_pno_level_segment(
    ax: plt.Axes,
    *,
    timestamps: np.ndarray,
    start_timestamp_ms: int | None,
    end_timestamp_ms: int | None,
    value: float | None,
    color: str,
    linewidth: float = 1.1,
    alpha: float = 0.75,
    linestyle: str = "-",
    zorder: float = 3.5,
) -> None:
    if value is None or start_timestamp_ms is None or end_timestamp_ms is None or timestamps.size == 0:
        return
    if end_timestamp_ms < start_timestamp_ms:
        end_timestamp_ms = start_timestamp_ms
    start_idx = int(np.searchsorted(timestamps, int(start_timestamp_ms), side="left"))
    end_idx = int(np.searchsorted(timestamps, int(end_timestamp_ms), side="right") - 1)
    start_idx = max(0, min(start_idx, len(timestamps) - 1))
    end_idx = max(start_idx, min(end_idx, len(timestamps) - 1))
    ax.hlines(
        float(value),
        start_idx - 0.45,
        end_idx + 0.45,
        color=color,
        linewidth=linewidth,
        alpha=alpha,
        linestyle=linestyle,
        zorder=zorder,
    )


def _format_pno_price_label(value: float | None) -> str:
    if value is None:
        return ""
    abs_value = abs(float(value))
    if abs_value >= 100.0:
        return f"{value:.2f}"
    if abs_value >= 1.0:
        return f"{value:.4f}"
    if abs_value >= 0.01:
        return f"{value:.5f}"
    return f"{value:.6f}"


def _format_pno_axis_tag_text(label: str, value: float | None) -> str:
    base = f"{label.upper():<{_PNO_PLOT_AXIS_TAG_LABEL_WIDTH}} {_format_pno_price_label(value)}"
    return f" {base:<{_PNO_PLOT_AXIS_TAG_TEXT_WIDTH}} "


def _annotate_pno_axis_price_tag(
    ax: plt.Axes,
    *,
    y: float | None,
    label: str,
    color: str,
    leader_start_x: float | None,
    leader_end_x: float | None = None,
    text_y: float | None = None,
    text_color: str = _PNO_PLOT_TEXT,
    alpha: float = 0.96,
) -> None:
    if y is None:
        return
    if text_y is None:
        text_y = y
    right_edge = float(ax.get_xlim()[1]) if leader_end_x is None else float(leader_end_x)
    if leader_start_x is not None and right_edge > float(leader_start_x):
        ax.hlines(
            float(y),
            float(leader_start_x),
            right_edge,
            color=color,
            linewidth=0.8,
            alpha=0.8,
            linestyle=(0, (1.2, 1.2)),
            zorder=6.2,
        )
    axis_transform = blended_transform_factory(ax.transAxes, ax.transData)
    ax.text(
        1.0,
        float(text_y),
        _format_pno_axis_tag_text(label, y),
        transform=axis_transform,
        ha="left",
        va="center",
        clip_on=False,
        fontsize=7,
        fontfamily="DejaVu Sans Mono",
        color=text_color,
        zorder=7.2,
        bbox={
            "boxstyle": "round,pad=0.16",
            "facecolor": _PNO_PLOT_AXIS_FACE,
            "edgecolor": color,
            "linewidth": 0.9,
            "alpha": alpha,
        },
    )


def _infer_pno_frame_step_ms(frame: pd.DataFrame, default_ms: int) -> int:
    if frame.empty or "timestamp" not in frame.columns:
        return default_ms
    timestamps = pd.to_numeric(frame["timestamp"], errors="coerce").dropna().to_numpy(dtype=np.int64)
    if timestamps.size < 2:
        return default_ms
    diffs = np.diff(np.sort(timestamps))
    diffs = diffs[diffs > 0]
    if diffs.size == 0:
        return default_ms
    return int(np.median(diffs))


def _format_pno_chart_symbol(symbol: str) -> str:
    base = str(symbol).split(":", 1)[0]
    if base.endswith("/USDT"):
        base = base[:-5]
    return base


def _plot_pno_marker(ax: plt.Axes, *, x: float, y: float | None, color: str, marker: str = "o") -> None:
    if y is None:
        return
    ax.scatter([x], [y], color=color, s=28, marker=marker, linewidths=0.9, zorder=7.4)


def _resolve_pno_axis_tag_positions(label_values: list[tuple[str, float | None]]) -> dict[str, float]:
    finite_pairs = [(label, float(value)) for label, value in label_values if value is not None and np.isfinite(float(value))]
    if not finite_pairs:
        return {}
    sorted_pairs = sorted(finite_pairs, key=lambda item: item[1], reverse=True)
    min_value = min(value for _, value in sorted_pairs)
    max_value = max(value for _, value in sorted_pairs)
    span = max(max_value - min_value, max_value * 0.01, 1e-9)
    min_gap = span * 0.07
    adjusted: dict[str, float] = {}
    previous = float("inf")
    for label, value in sorted_pairs:
        current = min(value, previous - min_gap)
        adjusted[label] = current
        previous = current
    return adjusted


def _pno_prices_close(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return False
    scale = max(abs(float(left)), abs(float(right)), 1.0)
    return abs(float(left) - float(right)) <= (scale * 1e-4)


def _annotate_pno_price_level(
    ax: plt.Axes,
    *,
    x: float,
    y: float | None,
    label: str,
    color: str,
    text_color: str = _PNO_PLOT_TEXT,
    alpha: float = 0.95,
) -> None:
    if y is None:
        return
    ax.text(
        float(x),
        float(y),
        f" {label} {_format_pno_price_label(y)} ",
        ha="left",
        va="center",
        fontsize=7,
        color=text_color,
        zorder=7,
        bbox={
            "boxstyle": "round,pad=0.18",
            "facecolor": _PNO_PLOT_AXIS_FACE,
            "edgecolor": color,
            "linewidth": 0.9,
            "alpha": alpha,
        },
    )


def _annotate_pno_point(
    ax: plt.Axes,
    *,
    x: float,
    y: float | None,
    label: str,
    color: str,
    marker: str = "o",
    dy_points: float = 8.0,
) -> None:
    if y is None:
        return
    ax.scatter([x], [y], color=color, s=28, marker=marker, zorder=7)
    ax.annotate(
        f"{label} {_format_pno_price_label(y)}",
        xy=(x, y),
        xytext=(4, dy_points),
        textcoords="offset points",
        fontsize=7,
        color=_PNO_PLOT_TEXT,
        bbox={
            "boxstyle": "round,pad=0.18",
            "facecolor": _PNO_PLOT_AXIS_FACE,
            "edgecolor": color,
            "linewidth": 0.9,
            "alpha": 0.95,
        },
        zorder=8,
    )


def _configure_pno_plot_axes(*, price_ax: plt.Axes, volume_ax: plt.Axes) -> None:
    for axis in (price_ax, volume_ax):
        axis.set_facecolor(_PNO_PLOT_AXIS_FACE)
        axis.grid(True, color=_PNO_PLOT_GRID, linewidth=0.7, alpha=0.18)
        axis.tick_params(axis="both", colors=_PNO_PLOT_MUTED, labelsize=8, length=0, pad=6)
        for spine in axis.spines.values():
            spine.set_color(_PNO_PLOT_PANEL_EDGE)
            spine.set_linewidth(1.0)
    price_ax.yaxis.label.set_color(_PNO_PLOT_MUTED)
    volume_ax.yaxis.label.set_color(_PNO_PLOT_MUTED)
    price_ax.yaxis.set_major_locator(LinearLocator(6))
    volume_ax.yaxis.set_major_locator(LinearLocator(3))
    price_ax.yaxis.set_label_coords(-0.072, 0.5)
    volume_ax.yaxis.set_label_coords(-0.072, 0.5)
    price_ax.yaxis.set_ticks_position("right")
    price_ax.yaxis.tick_right()
    price_ax.tick_params(axis="y", labelleft=False, labelright=True)


def _resolve_pno_pump_plot_idx(
    *,
    timestamps: np.ndarray,
    pump_start_timestamp_ms: int | None,
    ema9: np.ndarray | None,
    ema20: np.ndarray | None,
) -> int | None:
    if pump_start_timestamp_ms is None or timestamps.size == 0:
        return None
    pump_idx = int(np.searchsorted(timestamps, int(pump_start_timestamp_ms), side="left"))
    if pump_idx >= len(timestamps):
        pump_idx = len(timestamps) - 1
    elif pump_idx > 0:
        left_idx = pump_idx - 1
        if abs(int(timestamps[left_idx]) - int(pump_start_timestamp_ms)) <= abs(int(timestamps[pump_idx]) - int(pump_start_timestamp_ms)):
            pump_idx = left_idx
    pump_idx = min(max(pump_idx, 0), len(timestamps) - 1)
    if ema9 is None or ema20 is None or len(ema9) != len(timestamps) or len(ema20) != len(timestamps):
        return pump_idx

    shifted_idx = pump_idx
    for idx in range(pump_idx, -1, -1):
        ema9_value = float(ema9[idx])
        ema20_value = float(ema20[idx])
        if not np.isfinite(ema9_value) or not np.isfinite(ema20_value):
            continue
        shifted_idx = idx
        if ema9_value < ema20_value:
            break
    return shifted_idx


def _resolve_pno_timestamp_plot_idx(timestamps: np.ndarray, target_timestamp_ms: int) -> int:
    if timestamps.size == 0:
        return 0
    candidate_idx = int(np.searchsorted(timestamps, int(target_timestamp_ms), side="left"))
    if candidate_idx >= len(timestamps):
        return len(timestamps) - 1
    if candidate_idx <= 0:
        return 0
    left_idx = candidate_idx - 1
    if abs(int(timestamps[left_idx]) - int(target_timestamp_ms)) <= abs(int(timestamps[candidate_idx]) - int(target_timestamp_ms)):
        return left_idx
    return candidate_idx


def _draw_pno_candles_on_columns(
    ax: plt.Axes,
    frame: pd.DataFrame,
    *,
    x_column: str,
    candle_width: float,
) -> None:
    x_values = frame[x_column].to_numpy(dtype=np.float64)
    opens = frame["open"].to_numpy(dtype=np.float64)
    highs = frame["high"].to_numpy(dtype=np.float64)
    lows = frame["low"].to_numpy(dtype=np.float64)
    closes = frame["close"].to_numpy(dtype=np.float64)
    up_mask = closes >= opens
    wick_segments = np.stack(
        [
            np.column_stack([x_values, lows]),
            np.column_stack([x_values, highs]),
        ],
        axis=1,
    )
    wick_colors = np.where(up_mask, _PNO_PLOT_UP, _PNO_PLOT_DOWN)
    ax.add_collection(
        LineCollection(
            wick_segments,
            colors=wick_colors.tolist(),
            linewidths=1.0,
            alpha=1.0,
            zorder=3,
        )
    )
    body_lows = np.minimum(opens, closes)
    body_heights = np.maximum(np.abs(closes - opens), 1e-9)
    body_patches = [
        Rectangle(
            (float(x_pos) - candle_width / 2.0, float(body_low)),
            candle_width,
            float(body_height),
        )
        for x_pos, body_low, body_height in zip(x_values, body_lows, body_heights, strict=False)
    ]
    ax.add_collection(
        PatchCollection(
            body_patches,
            facecolor=wick_colors.tolist(),
            edgecolor=wick_colors.tolist(),
            linewidth=0.8,
            alpha=1.0,
            zorder=4,
            match_original=False,
        )
    )


def _build_pno_tick_timestamps(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(frame["timestamp"].astype("int64"), unit="ms", utc=True)


def _build_pno_tick_positions_from_timestamps(timestamps: pd.Series, frame_length: int) -> np.ndarray:
    timestamp_index = pd.DatetimeIndex(timestamps)
    hour_positions = np.flatnonzero(timestamp_index.minute == 0).tolist()
    if len(hour_positions) >= 3:
        selected = hour_positions
    else:
        half_hour_positions = np.flatnonzero(np.isin(timestamp_index.minute, [0, 30])).tolist()
        if len(half_hour_positions) >= 3:
            selected = half_hour_positions
        else:
            step = max(frame_length // 6, 1)
            selected = list(range(0, frame_length, step))
            if selected[-1] != frame_length - 1:
                selected.append(frame_length - 1)
    if len(selected) > _PNO_PLOT_MAX_X_TICKS:
        source = list(selected)
        selected = list(np.unique(np.linspace(0, len(source) - 1, _PNO_PLOT_MAX_X_TICKS, dtype=int)))
        selected = [source[idx] for idx in selected if idx < len(source)]
    return np.asarray(sorted(set(selected)), dtype=int)


def _build_pno_tick_positions(frame: pd.DataFrame) -> np.ndarray:
    timestamps = _build_pno_tick_timestamps(frame)
    return _build_pno_tick_positions_from_timestamps(timestamps, len(frame))


def _build_pno_tick_labels_from_timestamps(timestamps: pd.Series, positions: np.ndarray) -> list[str]:
    selected = pd.DatetimeIndex(timestamps).take(positions)
    full_day_mask = (selected.hour == 0) & (selected.minute == 0)
    day_labels = selected.strftime("%m-%d")
    time_labels = selected.strftime("%H:%M")
    return np.where(full_day_mask, day_labels, time_labels).tolist()


def _build_pno_tick_labels(frame: pd.DataFrame, positions: np.ndarray) -> list[str]:
    return _build_pno_tick_labels_from_timestamps(_build_pno_tick_timestamps(frame), positions)


def _render_pno_trade_chart(
    *,
    charts_dir: Path,
    symbol: str,
    levels_frame: pd.DataFrame,
    entry_frame: pd.DataFrame,
    trade_row: dict[str, object],
    trade_index: int,
) -> Path | None:
    entry_timestamp_ms = _safe_int(trade_row.get("entry_timestamp_ms"))
    exit_timestamp_ms = _safe_int(trade_row.get("exit_timestamp_ms"))
    entry_confirmation_mode = str(trade_row.get("entry_confirmation_mode") or "")
    entry_plan_price = _safe_float(trade_row.get("entry_plan"))
    entry_price = _safe_float(trade_row.get("entry_price"))
    if entry_price is None:
        entry_price = _safe_float(trade_row.get("entry_price_actual"))
    stop_loss = _safe_float(trade_row.get("sl_plan"))
    if stop_loss is None:
        stop_loss = _safe_float(trade_row.get("sl_actual"))
    tp1 = _safe_float(trade_row.get("tp1"))
    tp2 = _safe_float(trade_row.get("tp2"))
    pump_start_timestamp_ms = _safe_int(trade_row.get("pump_start_timestamp_ms")) or entry_timestamp_ms
    level_price = _safe_float(trade_row.get("level"))
    level_first_timestamp_ms = _safe_int(trade_row.get("level_first_local_high_timestamp_ms"))
    active_high = _safe_float(trade_row.get("active_high"))
    pullback_low = _safe_float(trade_row.get("pullback_low"))
    active_high_timestamp_ms = _safe_int(trade_row.get("active_high_timestamp_ms"))
    pullback_low_timestamp_ms = _safe_int(trade_row.get("pullback_low_timestamp_ms"))
    if (
        entry_timestamp_ms is None
        or exit_timestamp_ms is None
        or entry_price is None
        or stop_loss is None
        or pump_start_timestamp_ms is None
    ):
        return None
    display_entry_price = entry_price
    if entry_confirmation_mode == "cross":
        display_entry_price = level_price or entry_plan_price or entry_price
    else:
        display_entry_price = entry_plan_price or entry_price

    category = str(trade_row.get("category") or "")
    result_type = str(trade_row.get("result_type") or "")
    target_price = tp2 if category == "tp2" and tp2 is not None else tp1
    if target_price is None:
        target_price = max(display_entry_price, _safe_float(trade_row.get("exit_price")) or display_entry_price)
    show_high_tag = not _pno_prices_close(active_high, target_price)
    show_pullback_low_tag = not _pno_prices_close(pullback_low, stop_loss)

    levels_step_ms = _infer_pno_frame_step_ms(levels_frame, default_ms=5 * 60 * 1000)
    sleep_lookback_ms = _PNO_TRADE_SLEEP_LOOKBACK_BARS * levels_step_ms
    window_start_ms = max(pump_start_timestamp_ms - sleep_lookback_ms, 0)
    window_end_ms = max(exit_timestamp_ms + (30 * 60 * 1000), entry_timestamp_ms + (30 * 60 * 1000))
    plot_frame = _build_pno_plot_frame(
        levels_frame=levels_frame,
        entry_frame=entry_frame,
        start_timestamp_ms=window_start_ms,
        end_timestamp_ms=window_end_ms,
    )
    if plot_frame.empty:
        return None

    x_values = np.arange(len(plot_frame), dtype=np.float64)
    timestamps = plot_frame["timestamp"].to_numpy(dtype=np.int64)
    levels_window = levels_frame.loc[
        (levels_frame["timestamp"] >= window_start_ms)
        & (levels_frame["timestamp"] <= window_end_ms),
        ["timestamp", "open", "high", "low", "close", "volume"],
    ].copy()
    if not levels_window.empty:
        level_positions = np.interp(
            levels_window["timestamp"].to_numpy(dtype=np.float64),
            timestamps.astype(np.float64),
            x_values,
        )
        levels_window["plot_x"] = level_positions
    volume = plot_frame["volume"].fillna(0.0).to_numpy(dtype=np.float64)
    opens = plot_frame["open"].to_numpy(dtype=np.float64)
    closes = plot_frame["close"].to_numpy(dtype=np.float64)
    ema9 = plot_frame["ema9"].to_numpy(dtype=np.float64)
    ema20 = plot_frame["ema20"].to_numpy(dtype=np.float64)
    high_values = plot_frame["high"].to_numpy(dtype=np.float64)
    low_values = plot_frame["low"].to_numpy(dtype=np.float64)
    entry_idx = int(np.searchsorted(timestamps, entry_timestamp_ms, side="left"))
    exit_idx = int(np.searchsorted(timestamps, exit_timestamp_ms, side="left"))
    pump_idx = _resolve_pno_pump_plot_idx(
        timestamps=timestamps,
        pump_start_timestamp_ms=pump_start_timestamp_ms,
        ema9=ema9,
        ema20=ema20,
    )
    entry_idx = min(max(entry_idx, 0), len(plot_frame) - 1)
    exit_idx = min(max(exit_idx, entry_idx), len(plot_frame) - 1)
    if pump_idx is None:
        pump_idx = entry_idx
    rect_width = max(float(exit_idx - entry_idx + 1), 1.0)
    price_axis_right_x = float(len(plot_frame) - 0.5)

    fig, (ax_price, ax_levels, ax_volume) = plt.subplots(
        3,
        1,
        figsize=_PNO_TRADE_CHART_FIGSIZE,
        dpi=100,
        sharex=True,
        gridspec_kw={"height_ratios": _PNO_TRADE_CHART_HEIGHT_RATIOS, "hspace": 0.05},
        facecolor=_PNO_PLOT_FIGURE_FACE,
    )
    _configure_pno_plot_axes(price_ax=ax_price, volume_ax=ax_volume)
    _configure_pno_plot_axes(price_ax=ax_levels, volume_ax=ax_volume)

    _draw_pno_candles(ax_price, plot_frame, x_values)
    ax_price.set_title(_format_pno_chart_symbol(symbol), loc="left", color=_PNO_PLOT_TEXT, fontsize=11, pad=10, fontweight="semibold")
    if not levels_window.empty:
        _draw_pno_candles_on_columns(ax_levels, levels_window, x_column="plot_x", candle_width=_PNO_PLOT_5M_CANDLE_WIDTH)
    ax_price.plot(x_values, ema9, color=_PNO_PLOT_EMA9, linewidth=1.2, alpha=0.28, zorder=2.2)
    ax_price.plot(x_values, ema20, color=_PNO_PLOT_EMA20, linewidth=1.2, alpha=0.24, zorder=2.1)
    for axis in (ax_levels, ax_price):
        axis.axvline(pump_idx, color=_PNO_PLOT_PUMP, linewidth=1.15, alpha=0.82, zorder=5)

    tag_positions = _resolve_pno_axis_tag_positions(
        [
            ("TP", target_price),
            ("High", active_high if show_high_tag else None),
            ("Entry", display_entry_price),
            ("Level", level_price),
            ("SL", stop_loss),
            ("PB Low", pullback_low if show_pullback_low_tag else None),
            ("Exit", _safe_float(trade_row.get("exit_price_actual")) or _safe_float(trade_row.get("exit_price"))),
        ]
    )
    if level_price is not None:
        level_start_idx = entry_idx
        if level_first_timestamp_ms is not None:
            level_start_idx = int(np.searchsorted(timestamps, level_first_timestamp_ms, side="left"))
            level_start_idx = min(max(level_start_idx, 0), entry_idx)
        ax_price.hlines(
            level_price,
            level_start_idx - 0.48,
            entry_idx + 0.48,
            colors=_PNO_PLOT_LEVEL,
            linewidth=1.4,
            alpha=0.9,
            zorder=5,
        )
        ax_levels.hlines(
            level_price,
            level_start_idx - 0.48,
            entry_idx + 0.48,
            colors=_PNO_PLOT_LEVEL,
            linewidth=1.2,
            alpha=0.72,
            zorder=5,
        )
        _annotate_pno_axis_price_tag(
            ax_price,
            y=level_price,
            label="Level",
            color=_PNO_PLOT_LEVEL,
            leader_start_x=float(level_start_idx - 0.48),
            leader_end_x=price_axis_right_x,
            text_y=tag_positions.get("Level"),
        )
    if active_high is not None:
        high_idx = entry_idx
        if active_high_timestamp_ms is not None:
            high_idx = int(np.searchsorted(timestamps, active_high_timestamp_ms, side="left"))
            high_idx = min(max(high_idx, 0), len(plot_frame) - 1)
        _draw_pno_level_segment(
            ax_price,
            timestamps=timestamps,
            start_timestamp_ms=active_high_timestamp_ms,
            end_timestamp_ms=entry_timestamp_ms,
            value=active_high,
            color="#ef4444",
            linewidth=1.05,
            alpha=0.68,
        )
        if show_high_tag:
            _annotate_pno_axis_price_tag(
                ax_price,
                y=active_high,
                label="High",
                color="#ef4444",
                leader_start_x=float(high_idx),
                leader_end_x=price_axis_right_x,
                text_y=tag_positions.get("High"),
            )
    if pullback_low is not None:
        low_idx = entry_idx
        if pullback_low_timestamp_ms is not None:
            low_idx = int(np.searchsorted(timestamps, pullback_low_timestamp_ms, side="left"))
            low_idx = min(max(low_idx, 0), len(plot_frame) - 1)
        _draw_pno_level_segment(
            ax_price,
            timestamps=timestamps,
            start_timestamp_ms=pullback_low_timestamp_ms,
            end_timestamp_ms=entry_timestamp_ms,
            value=pullback_low,
            color="#38bdf8",
            linewidth=1.0,
            alpha=0.68,
        )
        if show_pullback_low_tag:
            _annotate_pno_axis_price_tag(
                ax_price,
                y=pullback_low,
                label="PB Low",
                color="#38bdf8",
                leader_start_x=float(low_idx),
                leader_end_x=price_axis_right_x,
                text_y=tag_positions.get("PB Low"),
            )
    ax_price.add_patch(
        Rectangle(
            (entry_idx - 0.5, min(stop_loss, display_entry_price)),
            rect_width,
            abs(display_entry_price - stop_loss),
            facecolor=_PNO_PLOT_RISK_FACE,
            edgecolor=_PNO_PLOT_RISK_EDGE,
            linewidth=0.9,
            alpha=0.32,
            zorder=1,
        )
    )
    ax_price.add_patch(
        Rectangle(
            (entry_idx - 0.5, min(display_entry_price, target_price)),
            rect_width,
            abs(target_price - display_entry_price),
            facecolor=_PNO_PLOT_PROFIT_FACE,
            edgecolor=_PNO_PLOT_PROFIT_EDGE,
            linewidth=0.9,
            alpha=0.28,
            zorder=1,
        )
    )
    _annotate_pno_axis_price_tag(
        ax_price,
        y=target_price,
        label="TP",
        color=_PNO_PLOT_PROFIT_EDGE,
        leader_start_x=float(entry_idx - 0.5),
        leader_end_x=price_axis_right_x,
        text_y=tag_positions.get("TP"),
        alpha=0.92,
    )
    _annotate_pno_axis_price_tag(
        ax_price,
        y=stop_loss,
        label="SL",
        color=_PNO_PLOT_RISK_EDGE,
        leader_start_x=float(entry_idx - 0.5),
        leader_end_x=price_axis_right_x,
        text_y=tag_positions.get("SL"),
        alpha=0.92,
    )
    _annotate_pno_axis_price_tag(
        ax_price,
        y=display_entry_price,
        label="Entry",
        color=_PNO_PLOT_ENTRY,
        leader_start_x=float(entry_idx),
        leader_end_x=price_axis_right_x,
        text_y=tag_positions.get("Entry"),
        alpha=0.9,
    )
    exit_price = _safe_float(trade_row.get("exit_price_actual"))
    if exit_price is None:
        exit_price = _safe_float(trade_row.get("exit_price"))
    if exit_price is not None:
        _annotate_pno_axis_price_tag(
            ax_price,
            y=exit_price,
            label="Exit",
            color=_PNO_PLOT_EXIT,
            leader_start_x=float(exit_idx),
            leader_end_x=price_axis_right_x,
            text_y=tag_positions.get("Exit"),
            alpha=0.9,
        )

    if not levels_window.empty:
        levels_volume = levels_window["volume"].fillna(0.0).to_numpy(dtype=np.float64)
        levels_open = levels_window["open"].to_numpy(dtype=np.float64)
        levels_close = levels_window["close"].to_numpy(dtype=np.float64)
        levels_volume_max = float(np.nanmax(levels_volume)) if len(levels_volume) > 0 else 0.0
        levels_volume_pct = (levels_volume / levels_volume_max) * 100.0 if levels_volume_max > 0.0 else np.zeros_like(levels_volume)
        levels_volume_colors = np.where(levels_close >= levels_open, _PNO_PLOT_UP, _PNO_PLOT_DOWN)
        ax_volume.bar(
            levels_window["plot_x"].to_numpy(dtype=np.float64),
            levels_volume_pct,
            width=_PNO_PLOT_5M_CANDLE_WIDTH,
            color=levels_volume_colors,
            edgecolor="none",
            alpha=0.88,
            zorder=3,
        )

    padding = max((float(np.nanmax(high_values)) - float(np.nanmin(low_values))) * 0.05, 1e-9)
    ax_price.set_ylim(float(np.nanmin(low_values)) - padding, float(np.nanmax(high_values)) + padding)
    ax_price.set_xlim(-0.5, len(plot_frame) - 0.5)
    if not levels_window.empty:
        levels_high = levels_window["high"].to_numpy(dtype=np.float64)
        levels_low = levels_window["low"].to_numpy(dtype=np.float64)
        levels_padding = max((float(np.nanmax(levels_high)) - float(np.nanmin(levels_low))) * 0.08, 1e-9)
        ax_levels.set_ylim(float(np.nanmin(levels_low)) - levels_padding, float(np.nanmax(levels_high)) + levels_padding)
    ax_levels.set_xlim(-0.5, len(plot_frame) - 0.5)
    ax_price.set_ylabel("1m")
    ax_levels.set_ylabel("5m")
    ax_volume.set_ylabel("Vol %")
    ax_levels.yaxis.set_label_coords(-0.072, 0.5)
    ax_volume.set_ylim(0.0, 100.0)
    ax_volume.set_yticks([0.0, 50.0, 100.0])
    ax_volume.set_yticklabels(["0", "50", "100"], color=_PNO_PLOT_MUTED)
    tick_timestamps = _build_pno_tick_timestamps(plot_frame)
    tick_positions = _build_pno_tick_positions_from_timestamps(tick_timestamps, len(plot_frame))
    tick_labels = _build_pno_tick_labels_from_timestamps(tick_timestamps, tick_positions)
    ax_volume.set_xticks(tick_positions)
    ax_volume.set_xticklabels(tick_labels)
    ax_price.tick_params(axis="x", labelbottom=False)
    ax_levels.tick_params(axis="x", labelbottom=False)
    ax_levels.margins(x=0.0)
    ax_price.margins(x=0.01)
    ax_volume.margins(x=0.0)

    file_name = f"{_sanitize_plot_name(symbol.replace('/', '_'))}_{trade_index:03d}_{_sanitize_plot_name(result_type.lower() or category.lower() or 'trade')}.png"
    output_path = charts_dir / file_name
    fig.subplots_adjust(left=0.10, right=0.80, top=0.94, bottom=0.06, hspace=0.05)
    fig.savefig(output_path, dpi=100, facecolor=_PNO_PLOT_FIGURE_FACE)
    plt.close(fig)
    return output_path


def _render_pno_trade_charts_for_symbol(
    *,
    charts_dir: Path,
    symbol: str,
    mtf_frames: SymbolMtfFrames,
    trade_rows: list[dict[str, object]],
) -> list[str]:
    if not trade_rows:
        return []
    levels_plot_frame = _prepare_pno_levels_plot_source(mtf_frames.levels_frame)
    entry_plot_frame = _prepare_pno_entry_plot_source_with_ema(
        levels_frame=levels_plot_frame,
        entry_frame=mtf_frames.entry_frame,
    )
    chart_paths: list[str] = []
    for trade_index, trade_row in enumerate(trade_rows, start=1):
        chart_path = _render_pno_trade_chart(
            charts_dir=charts_dir,
            symbol=symbol,
            levels_frame=levels_plot_frame,
            entry_frame=entry_plot_frame,
            trade_row=trade_row,
            trade_index=trade_index,
        )
        if chart_path is not None:
            chart_paths.append(str(chart_path))
    return chart_paths


def _render_pno_stage_review_chart(
    *,
    charts_dir: Path,
    symbol: str,
    levels_frame: pd.DataFrame,
    entry_frame: pd.DataFrame,
    review_row: dict[str, object],
    review_index: int,
    status: str = "review",
    reason: str | None = None,
) -> str | None:
    stage_id = str(review_row.get("stage_id", "stage"))
    current_timestamp_ms = _safe_int(review_row.get("current_timestamp_ms"))
    timestamp_ms = current_timestamp_ms or _safe_int(review_row.get("timestamp_ms"))
    if timestamp_ms is None:
        return None
    pump_start_timestamp_ms = _safe_int(review_row.get("pump_start_timestamp_ms"))
    sleep_start_timestamp_ms = _safe_int(review_row.get("sleep_start_timestamp_ms"))
    is_stage1 = stage_id == PNO_STAGE_SEQUENCE[0]
    use_levels_frame = stage_id in PNO_STAGE_SEQUENCE[:3]
    if is_stage1 and pump_start_timestamp_ms is not None:
        start_timestamp_ms = sleep_start_timestamp_ms or max(pump_start_timestamp_ms - (120 * 60_000), 0)
        end_timestamp_ms = timestamp_ms
    else:
        start_timestamp_ms = timestamp_ms - (90 * 60_000)
        end_timestamp_ms = timestamp_ms
    if use_levels_frame:
        plot_frame = levels_frame.loc[
            (levels_frame["timestamp"] >= start_timestamp_ms)
            & (levels_frame["timestamp"] <= end_timestamp_ms)
        ].copy()
    else:
        plot_frame = _build_pno_plot_frame(
            levels_frame=levels_frame,
            entry_frame=entry_frame,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=end_timestamp_ms,
        )
    if plot_frame.empty:
        return None

    figure_width = 8.0
    figure_height = 4.5
    fig, (ax_price, ax_volume) = plt.subplots(
        2,
        1,
        figsize=(figure_width, figure_height),
        dpi=72,
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    fig.patch.set_facecolor(_PNO_PLOT_FIGURE_FACE)
    _configure_pno_plot_axes(price_ax=ax_price, volume_ax=ax_volume)

    opens = plot_frame["open"].to_numpy(dtype=np.float64)
    close_values = plot_frame["close"].to_numpy(dtype=np.float64)
    high_values = plot_frame["high"].to_numpy(dtype=np.float64)
    low_values = plot_frame["low"].to_numpy(dtype=np.float64)
    volume_values = plot_frame["volume"].fillna(0.0).to_numpy(dtype=np.float64)
    x = np.arange(len(plot_frame), dtype=np.float64)
    ema9 = plot_frame["ema9"].to_numpy(dtype=np.float64) if "ema9" in plot_frame.columns else np.full(len(plot_frame), np.nan, dtype=np.float64)
    ema20 = plot_frame["ema20"].to_numpy(dtype=np.float64) if "ema20" in plot_frame.columns else np.full(len(plot_frame), np.nan, dtype=np.float64)
    timestamps = plot_frame["timestamp"].to_numpy(dtype=np.int64)
    event_idx = _resolve_pno_timestamp_plot_idx(timestamps, int(timestamp_ms))
    pump_idx = _resolve_pno_pump_plot_idx(
        timestamps=timestamps,
        pump_start_timestamp_ms=pump_start_timestamp_ms,
        ema9=ema9,
        ema20=ema20,
    )

    _draw_pno_candles(ax_price, plot_frame, x)
    if (not is_stage1) and np.isfinite(ema9).any():
        ax_price.plot(x, ema9, color=_PNO_PLOT_EMA9, linewidth=0.8, alpha=0.22, zorder=2)
    if (not is_stage1) and np.isfinite(ema20).any():
        ax_price.plot(x, ema20, color=_PNO_PLOT_EMA20, linewidth=0.8, alpha=0.20, zorder=2)
    if pump_idx is not None:
        ax_price.axvline(pump_idx, color=_PNO_PLOT_PUMP, linewidth=1.0, alpha=0.86, zorder=5)
        ax_volume.axvline(pump_idx, color=_PNO_PLOT_PUMP, linewidth=1.0, alpha=0.72, zorder=4)

    if not is_stage1:
        active_high = _safe_float(review_row.get("active_high"))
        pullback_low = _safe_float(review_row.get("pullback_low"))
        level_price = _safe_float(review_row.get("level"))
        entry_price = _safe_float(review_row.get("entry_price"))
        stage1_hold_price = _safe_float(review_row.get("stage1_hold_price"))
        active_high_timestamp_ms = _safe_int(review_row.get("active_high_timestamp_ms"))
        pullback_low_timestamp_ms = _safe_int(review_row.get("pullback_low_timestamp_ms"))
        level_first_timestamp_ms = _safe_int(review_row.get("level_first_local_high_timestamp_ms"))
        level_last_timestamp_ms = _safe_int(review_row.get("level_valid_timestamp_ms")) or timestamp_ms
        event_timestamp_ms = int(timestamp_ms)

        _draw_pno_level_segment(
            ax_price,
            timestamps=timestamps,
            start_timestamp_ms=active_high_timestamp_ms,
            end_timestamp_ms=event_timestamp_ms,
            value=active_high,
            color="#ef4444",
            linewidth=1.1,
            alpha=0.72,
        )
        if active_high_timestamp_ms is not None:
            active_high_idx = int(np.searchsorted(timestamps, int(active_high_timestamp_ms), side="left"))
            active_high_idx = min(max(active_high_idx, 0), len(plot_frame) - 1)
            _annotate_pno_point(
                ax_price,
                x=float(active_high_idx),
                y=active_high,
                label="High",
                color="#ef4444",
                marker="^",
                dy_points=8.0,
            )
        _draw_pno_level_segment(
            ax_price,
            timestamps=timestamps,
            start_timestamp_ms=pullback_low_timestamp_ms,
            end_timestamp_ms=event_timestamp_ms,
            value=pullback_low,
            color="#38bdf8",
            linewidth=1.0,
            alpha=0.68,
        )
        if pullback_low_timestamp_ms is not None:
            pullback_low_idx = int(np.searchsorted(timestamps, int(pullback_low_timestamp_ms), side="left"))
            pullback_low_idx = min(max(pullback_low_idx, 0), len(plot_frame) - 1)
            _annotate_pno_point(
                ax_price,
                x=float(pullback_low_idx),
                y=pullback_low,
                label="PB Low",
                color="#38bdf8",
                marker="v",
                dy_points=-14.0,
            )
        _draw_pno_level_segment(
            ax_price,
            timestamps=timestamps,
            start_timestamp_ms=level_first_timestamp_ms,
            end_timestamp_ms=level_last_timestamp_ms,
            value=level_price,
            color=_PNO_PLOT_LEVEL,
            linewidth=1.25,
            alpha=0.85,
        )
        _annotate_pno_price_level(
            ax_price,
            x=min(float(len(plot_frame) - 1.2), float(event_idx + 1.0)),
            y=level_price,
            label="Level",
            color=_PNO_PLOT_LEVEL,
        )
        _draw_pno_level_segment(
            ax_price,
            timestamps=timestamps,
            start_timestamp_ms=level_first_timestamp_ms,
            end_timestamp_ms=event_timestamp_ms,
            value=entry_price,
            color=_PNO_PLOT_ENTRY,
            linewidth=0.95,
            alpha=0.45,
            linestyle="--",
        )
        if entry_price is not None:
            _annotate_pno_price_level(
                ax_price,
                x=min(float(len(plot_frame) - 1.2), float(event_idx + 1.0)),
                y=entry_price,
                label="Entry",
                color=_PNO_PLOT_ENTRY,
                alpha=0.88,
            )
        _draw_pno_level_segment(
            ax_price,
            timestamps=timestamps,
            start_timestamp_ms=pump_start_timestamp_ms,
            end_timestamp_ms=event_timestamp_ms,
            value=stage1_hold_price,
            color="#22d3ee",
            linewidth=0.9,
            alpha=0.35,
            linestyle=":",
            zorder=2.6,
        )

    if volume_values.size > 0:
        volume_max = float(np.nanmax(volume_values)) if np.isfinite(volume_values).any() else 0.0
        volume_pct = (volume_values / volume_max) * 100.0 if volume_max > 0.0 else np.zeros_like(volume_values)
        up_mask = close_values >= opens
        volume_colors = np.where(up_mask, _PNO_PLOT_UP, _PNO_PLOT_DOWN)
        ax_volume.bar(x, volume_pct, width=0.82, color=volume_colors, edgecolor="none", alpha=0.8, zorder=2)

    if np.isfinite(high_values).any() and np.isfinite(low_values).any():
        padding = max((float(np.nanmax(high_values)) - float(np.nanmin(low_values))) * 0.06, 1e-9)
        ax_price.set_ylim(float(np.nanmin(low_values)) - padding, float(np.nanmax(high_values)) + padding)
    ax_price.set_xlim(-0.5, len(plot_frame) - 0.5)
    ax_volume.set_ylim(0.0, 100.0)
    ax_volume.set_yticks([0.0, 50.0, 100.0])
    ax_volume.set_yticklabels(["0", "50", "100"], color=_PNO_PLOT_MUTED)
    ax_price.set_ylabel("5m" if use_levels_frame else "1m", color=_PNO_PLOT_MUTED, fontsize=8)
    ax_volume.set_ylabel("Vol %", color=_PNO_PLOT_MUTED, fontsize=8)

    tick_timestamps = _build_pno_tick_timestamps(plot_frame)
    tick_positions = _build_pno_tick_positions_from_timestamps(tick_timestamps, len(plot_frame))
    tick_labels = _build_pno_tick_labels_from_timestamps(tick_timestamps, tick_positions)
    ax_volume.set_xticks(tick_positions)
    ax_volume.set_xticklabels(tick_labels, fontsize=8, color=_PNO_PLOT_MUTED)
    ax_price.tick_params(axis="x", labelbottom=False)

    sanitized_status = _sanitize_plot_name(status)
    sanitized_reason = _sanitize_plot_name(reason if reason is not None else str(review_row.get("reason", "ok")))
    file_name = f"{_sanitize_plot_name(symbol.replace('/', '_'))}_{review_index:04d}_{_sanitize_plot_name(stage_id)}_{sanitized_status}_{sanitized_reason}.png"
    output_path = charts_dir / file_name
    fig.subplots_adjust(left=0.08, right=0.985, top=0.985, bottom=0.10, hspace=0.04)
    fig.savefig(output_path, dpi=72, facecolor=_PNO_PLOT_FIGURE_FACE)
    plt.close(fig)
    return str(output_path)


def _export_pno_stage_reviews(
    *,
    diagnostics_dir: Path,
    symbol_frames: dict[str, SymbolMtfFrames],
    stage_rows_by_stage: dict[str, list[dict[str, object]]],
    stage_rejections_by_stage: dict[str, dict[str, list[dict[str, object]]]],
    selected_stage_ids: tuple[str, ...],
    logger: Logger | None = None,
    log_prefix: str = "pno",
) -> None:
    stage_reviews_dir = diagnostics_dir / "stage_reviews"
    stage_reviews_dir.mkdir(parents=True, exist_ok=True)
    chart_stage_ids = set(PNO_STAGE_SEQUENCE)
    manifest_rows: list[dict[str, object]] = []
    selected_stage_set = set(selected_stage_ids)
    needs_entry_frames = bool(selected_stage_set.intersection(PNO_STAGE_SEQUENCE[3:]))
    prepared_frames_by_symbol: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    total_review_charts = 0
    for stage_id in selected_stage_ids:
        total_review_charts += len(stage_rows_by_stage.get(stage_id, []))
        rejection_groups = stage_rejections_by_stage.get(stage_id, {})
        total_review_charts += sum(len(rows) for rows in rejection_groups.values())
    rendered_review_charts = 0
    review_start_time = time.monotonic()

    def _log_review_progress(stage_id: str) -> None:
        if logger is None or total_review_charts <= 0:
            return
        if rendered_review_charts <= 0:
            return
        if rendered_review_charts == total_review_charts or rendered_review_charts % 50 == 0:
            elapsed = max(time.monotonic() - review_start_time, 1e-9)
            rate = rendered_review_charts / elapsed
            remaining = total_review_charts - rendered_review_charts
            eta_seconds = remaining / rate if rate > 0.0 else None
            logger.info(
                "%s: pno stage-review charts %s/%s (%.1f%%) current_stage=%s eta=%s",
                log_prefix,
                rendered_review_charts,
                total_review_charts,
                (rendered_review_charts / total_review_charts) * 100.0,
                stage_id,
                _format_eta_compact(eta_seconds),
            )

    if logger is not None and total_review_charts > 0:
        logger.info(
            "%s: pno stage-review charts start total=%s stages=%s",
            log_prefix,
            total_review_charts,
            ",".join(selected_stage_ids),
        )

    def _get_prepared_frames(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame] | None:
        cached = prepared_frames_by_symbol.get(symbol)
        if cached is not None:
            return cached
        mtf_frames = symbol_frames.get(symbol)
        if mtf_frames is None:
            return None
        levels_prepared = _prepare_pno_levels_plot_source(mtf_frames.levels_frame)
        prepared = (
            levels_prepared,
            _prepare_pno_entry_plot_source_with_ema(
                levels_frame=levels_prepared,
                entry_frame=mtf_frames.entry_frame,
            )
            if needs_entry_frames
            else mtf_frames.entry_frame,
        )
        prepared_frames_by_symbol[symbol] = prepared
        return prepared

    for stage_id in selected_stage_ids:
        stage_dir = stage_reviews_dir / stage_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        passed_rows = stage_rows_by_stage.get(stage_id, [])
        passed_frame = pd.DataFrame(passed_rows)
        passed_dir = stage_dir / "passed"
        passed_dir.mkdir(parents=True, exist_ok=True)
        passed_frame.to_csv(passed_dir / "events.csv", index=False)
        passed_chart_paths: list[str] = []
        if stage_id in chart_stage_ids:
            passed_charts_dir = passed_dir / "charts"
            passed_charts_dir.mkdir(parents=True, exist_ok=True)
            for row_index, row in enumerate(passed_rows, start=1):
                symbol = str(row.get("symbol", ""))
                prepared_frames = _get_prepared_frames(symbol)
                if prepared_frames is None:
                    continue
                chart_path = _render_pno_stage_review_chart(
                    charts_dir=passed_charts_dir,
                    symbol=symbol,
                    levels_frame=prepared_frames[0],
                    entry_frame=prepared_frames[1],
                    review_row=row,
                    review_index=row_index,
                    status="passed",
                    reason="passed",
                )
                if chart_path is not None:
                    passed_chart_paths.append(chart_path)
                rendered_review_charts += 1
                _log_review_progress(stage_id)

        rejection_groups = stage_rejections_by_stage.get(stage_id, {})
        rejected_total = 0
        rejected_chart_paths = 0
        rejected_dir = stage_dir / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        for reason, rows in sorted(rejection_groups.items()):
            rejected_total += len(rows)
            reason_dir = rejected_dir / _sanitize_plot_name(reason)
            reason_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_csv(reason_dir / "events.csv", index=False)
            if stage_id in chart_stage_ids:
                charts_dir = reason_dir / "charts"
                charts_dir.mkdir(parents=True, exist_ok=True)
                for row_index, row in enumerate(rows, start=1):
                    symbol = str(row.get("symbol", ""))
                    prepared_frames = _get_prepared_frames(symbol)
                    if prepared_frames is None:
                        continue
                    chart_path = _render_pno_stage_review_chart(
                        charts_dir=charts_dir,
                        symbol=symbol,
                        levels_frame=prepared_frames[0],
                        entry_frame=prepared_frames[1],
                        review_row=row,
                        review_index=row_index,
                        status="rejected",
                        reason=reason,
                    )
                    if chart_path is not None:
                        rejected_chart_paths += 1
                    rendered_review_charts += 1
                    _log_review_progress(stage_id)

        manifest_rows.append(
            {
                "stage_id": stage_id,
                "passed_count": int(len(passed_rows)),
                "rejected_count": int(rejected_total),
                "passed_events_path": str(passed_dir / "events.csv"),
                "passed_charts_count": int(len(passed_chart_paths)),
                "rejected_charts_count": int(rejected_chart_paths),
            }
        )

    pd.DataFrame(manifest_rows).to_csv(stage_reviews_dir / "manifest.csv", index=False)
    if logger is not None and total_review_charts > 0:
        logger.info(
            "%s: pno stage-review charts finished total=%s",
            log_prefix,
            rendered_review_charts,
        )


def _bucketize_pno_value(
    value: float | None,
    *,
    thresholds: tuple[float, ...],
    labels: tuple[str, ...],
    na_label: str = "na",
) -> str:
    if value is None or not np.isfinite(float(value)):
        return na_label
    numeric_value = float(value)
    for threshold, label in zip(thresholds, labels[:-1], strict=False):
        if numeric_value <= threshold:
            return label
    return labels[-1]


def _resolve_pno_stage_key_from_row(row: dict[str, object]) -> str | None:
    symbol = str(row.get("symbol") or "")
    active_high_timestamp_ms = _safe_int(row.get("active_high_timestamp_ms"))
    pullback_low_timestamp_ms = _safe_int(row.get("pullback_low_timestamp_ms"))
    level_valid_timestamp_ms = _safe_int(row.get("level_valid_timestamp_ms"))
    level = _safe_float(row.get("level"))
    if not symbol or active_high_timestamp_ms is None or pullback_low_timestamp_ms is None or level_valid_timestamp_ms is None or level is None:
        return None
    return f"{symbol}|{active_high_timestamp_ms}|{pullback_low_timestamp_ms}|{level_valid_timestamp_ms}|{level:.8f}"


def _resolve_pno_signal_bar_context(
    *,
    entry_frame: pd.DataFrame,
    timestamp_ms: int | None,
    level: float | None,
) -> dict[str, object]:
    if timestamp_ms is None or entry_frame.empty or "timestamp" not in entry_frame.columns:
        return {}
    timestamps = entry_frame["timestamp"].to_numpy(dtype=np.int64, copy=False)
    if timestamps.size == 0:
        return {}
    idx = _resolve_pno_timestamp_plot_idx(timestamps, int(timestamp_ms))
    open_price = float(entry_frame["open"].iloc[idx])
    high_price = float(entry_frame["high"].iloc[idx])
    low_price = float(entry_frame["low"].iloc[idx])
    close_price = float(entry_frame["close"].iloc[idx])
    volume = float(entry_frame["volume"].iloc[idx])
    quote_volume = close_price * volume
    bar_range = max(high_price - low_price, 1e-12)
    body = abs(close_price - open_price)
    upper_wick = high_price - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low_price
    recent_start = max(0, idx - 15)
    recent_slice = entry_frame["volume"].iloc[recent_start:idx]
    recent_volume_median = float(recent_slice.median()) if not recent_slice.empty else np.nan
    ema9 = float(entry_frame["ema9"].iloc[idx]) if "ema9" in entry_frame.columns else np.nan
    ema20 = float(entry_frame["ema20"].iloc[idx]) if "ema20" in entry_frame.columns else np.nan
    ema9_prev = float(entry_frame["ema9"].iloc[max(0, idx - 3)]) if "ema9" in entry_frame.columns else np.nan
    ema20_prev = float(entry_frame["ema20"].iloc[max(0, idx - 3)]) if "ema20" in entry_frame.columns else np.nan
    trade_count_column = next(
        (column for column in ("number_of_trades", "trades", "trade_count") if column in entry_frame.columns),
        None,
    )
    trade_count = float(entry_frame[trade_count_column].iloc[idx]) if trade_count_column is not None else np.nan
    recent_trade_slice = entry_frame[trade_count_column].iloc[recent_start:idx] if trade_count_column is not None else pd.Series(dtype=float)
    recent_trade_median = float(recent_trade_slice.median()) if not recent_trade_slice.empty else np.nan
    level_value = float(level) if level is not None and np.isfinite(float(level)) else np.nan
    signal_high_clearance_pct = ((high_price - level_value) / level_value * 100.0) if np.isfinite(level_value) and level_value > 0.0 else np.nan
    signal_close_clearance_pct = ((close_price - level_value) / level_value * 100.0) if np.isfinite(level_value) and level_value > 0.0 else np.nan
    signal_open_clearance_pct = ((open_price - level_value) / level_value * 100.0) if np.isfinite(level_value) and level_value > 0.0 else np.nan
    ema9_dist_pct = ((close_price - ema9) / ema9 * 100.0) if np.isfinite(ema9) and ema9 > 0.0 else np.nan
    ema20_dist_pct = ((close_price - ema20) / ema20 * 100.0) if np.isfinite(ema20) and ema20 > 0.0 else np.nan
    ema_spread_pct = ((ema9 - ema20) / ema20 * 100.0) if np.isfinite(ema9) and np.isfinite(ema20) and ema20 > 0.0 else np.nan
    ema9_slope_3 = ((ema9 - ema9_prev) / ema9_prev * 100.0) if np.isfinite(ema9) and np.isfinite(ema9_prev) and ema9_prev > 0.0 else np.nan
    ema20_slope_3 = ((ema20 - ema20_prev) / ema20_prev * 100.0) if np.isfinite(ema20) and np.isfinite(ema20_prev) and ema20_prev > 0.0 else np.nan
    future_slice = entry_frame.iloc[idx + 1 : idx + 4]
    signal_followthrough_1bar_pct = np.nan
    signal_followthrough_3bar_pct = np.nan
    signal_adverse_1bar_pct = np.nan
    signal_adverse_3bar_pct = np.nan
    if not future_slice.empty and close_price > 0.0:
        future_highs = pd.to_numeric(future_slice["high"], errors="coerce")
        future_lows = pd.to_numeric(future_slice["low"], errors="coerce")
        if not future_highs.empty:
            signal_followthrough_1bar_pct = ((float(future_highs.iloc[0]) - close_price) / close_price) * 100.0
            signal_followthrough_3bar_pct = ((float(future_highs.max()) - close_price) / close_price) * 100.0
        if not future_lows.empty:
            signal_adverse_1bar_pct = ((float(future_lows.iloc[0]) - close_price) / close_price) * 100.0
            signal_adverse_3bar_pct = ((float(future_lows.min()) - close_price) / close_price) * 100.0
    return {
        "signal_bar_timestamp_ms": int(timestamps[idx]),
        "signal_bar_open": round(open_price, 8),
        "signal_bar_high": round(high_price, 8),
        "signal_bar_low": round(low_price, 8),
        "signal_bar_close": round(close_price, 8),
        "signal_bar_volume": round(volume, 4),
        "signal_bar_quote_volume": round(quote_volume, 4),
        "signal_bar_range": round(bar_range, 8),
        "signal_bar_body": round(body, 8),
        "signal_bar_body_share": round(body / bar_range, 4),
        "signal_bar_upper_wick_share": round(max(upper_wick, 0.0) / bar_range, 4),
        "signal_bar_lower_wick_share": round(max(lower_wick, 0.0) / bar_range, 4),
        "signal_bar_close_position": round((close_price - low_price) / bar_range, 4),
        "signal_bar_is_green": bool(close_price >= open_price),
        "signal_bar_volume_vs_recent": round(volume / recent_volume_median, 4) if np.isfinite(recent_volume_median) and recent_volume_median > 0.0 else np.nan,
        "signal_bar_trade_count": round(trade_count, 4) if np.isfinite(trade_count) else np.nan,
        "signal_bar_trade_count_vs_recent": round(trade_count / recent_trade_median, 4) if np.isfinite(trade_count) and np.isfinite(recent_trade_median) and recent_trade_median > 0.0 else np.nan,
        "signal_bar_ema9_dist_pct": round(ema9_dist_pct, 4) if np.isfinite(ema9_dist_pct) else np.nan,
        "signal_bar_ema20_dist_pct": round(ema20_dist_pct, 4) if np.isfinite(ema20_dist_pct) else np.nan,
        "signal_bar_ema_spread_pct": round(ema_spread_pct, 4) if np.isfinite(ema_spread_pct) else np.nan,
        "signal_bar_ema9_slope_3": round(ema9_slope_3, 4) if np.isfinite(ema9_slope_3) else np.nan,
        "signal_bar_ema20_slope_3": round(ema20_slope_3, 4) if np.isfinite(ema20_slope_3) else np.nan,
        "signal_followthrough_1bar_pct": round(signal_followthrough_1bar_pct, 4) if np.isfinite(signal_followthrough_1bar_pct) else np.nan,
        "signal_followthrough_3bar_pct": round(signal_followthrough_3bar_pct, 4) if np.isfinite(signal_followthrough_3bar_pct) else np.nan,
        "signal_adverse_1bar_pct": round(signal_adverse_1bar_pct, 4) if np.isfinite(signal_adverse_1bar_pct) else np.nan,
        "signal_adverse_3bar_pct": round(signal_adverse_3bar_pct, 4) if np.isfinite(signal_adverse_3bar_pct) else np.nan,
        "signal_open_clearance_pct": round(signal_open_clearance_pct, 4) if np.isfinite(signal_open_clearance_pct) else np.nan,
        "signal_high_clearance_pct": round(signal_high_clearance_pct, 4) if np.isfinite(signal_high_clearance_pct) else np.nan,
        "signal_close_clearance_pct": round(signal_close_clearance_pct, 4) if np.isfinite(signal_close_clearance_pct) else np.nan,
    }


def _slice_pno_frame_by_timestamp(
    frame: pd.DataFrame,
    *,
    start_timestamp_ms: int | None,
    end_timestamp_ms: int | None,
) -> pd.DataFrame:
    if frame.empty or "timestamp" not in frame.columns or start_timestamp_ms is None or end_timestamp_ms is None:
        return pd.DataFrame()
    if end_timestamp_ms < start_timestamp_ms:
        end_timestamp_ms = start_timestamp_ms
    timestamps = frame["timestamp"].to_numpy(dtype=np.int64, copy=False)
    if timestamps.size == 0:
        return pd.DataFrame()
    start_idx = int(np.searchsorted(timestamps, int(start_timestamp_ms), side="left"))
    end_idx = int(np.searchsorted(timestamps, int(end_timestamp_ms), side="right"))
    if start_idx >= end_idx:
        return pd.DataFrame()
    return frame.iloc[start_idx:end_idx]


def _resolve_pno_session_bucket(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "na"
    hour = int(pd.Timestamp(timestamp_ms, unit="ms", tz="UTC").hour)
    if 0 <= hour < 7:
        return "asia"
    if 7 <= hour < 13:
        return "europe_open"
    if 13 <= hour < 17:
        return "us_premarket_overlap"
    if 17 <= hour < 22:
        return "us_main"
    return "late"


def _resolve_pno_window_profile(
    frame: pd.DataFrame,
    *,
    prefix: str,
    anchor_price: float | None = None,
) -> dict[str, object]:
    if frame.empty:
        return {}
    opens = frame["open"].to_numpy(dtype=np.float64, copy=False)
    highs = frame["high"].to_numpy(dtype=np.float64, copy=False)
    lows = frame["low"].to_numpy(dtype=np.float64, copy=False)
    closes = frame["close"].to_numpy(dtype=np.float64, copy=False)
    volumes = frame["volume"].to_numpy(dtype=np.float64, copy=False)
    bar_ranges = np.maximum(highs - lows, 1e-12)
    bodies = np.abs(closes - opens)
    green_mask = closes >= opens
    upper_wicks = np.maximum(highs - np.maximum(opens, closes), 0.0)
    lower_wicks = np.maximum(np.minimum(opens, closes) - lows, 0.0)
    path = np.abs(np.diff(closes)).sum() if closes.size > 1 else 0.0
    direct = abs(closes[-1] - closes[0]) if closes.size > 0 else 0.0
    alternation_rate = float(np.mean(green_mask[1:] != green_mask[:-1])) if green_mask.size > 1 else np.nan
    positive_close_share = float(np.mean(np.diff(closes) >= 0.0)) if closes.size > 1 else np.nan
    volume_chunks = np.array_split(volumes, 3) if volumes.size >= 3 else [volumes]
    first_chunk = volume_chunks[0] if volume_chunks else np.array([], dtype=np.float64)
    last_chunk = volume_chunks[-1] if volume_chunks else np.array([], dtype=np.float64)
    trade_count_column = next((column for column in ("number_of_trades", "trades", "trade_count") if column in frame.columns), None)
    trade_counts = frame[trade_count_column].to_numpy(dtype=np.float64, copy=False) if trade_count_column is not None else None
    oi_values = frame["open_interest"].to_numpy(dtype=np.float64, copy=False) if "open_interest" in frame.columns else None
    quote_volume = closes * volumes
    result: dict[str, object] = {
        f"{prefix}_bar_count": int(len(frame)),
        f"{prefix}_green_share": round(float(np.mean(green_mask)), 4),
        f"{prefix}_red_share": round(float(np.mean(~green_mask)), 4),
        f"{prefix}_alternation_rate": round(alternation_rate, 4) if np.isfinite(alternation_rate) else np.nan,
        f"{prefix}_positive_close_share": round(positive_close_share, 4) if np.isfinite(positive_close_share) else np.nan,
        f"{prefix}_body_share_avg": round(float(np.mean(bodies / bar_ranges)), 4),
        f"{prefix}_upper_wick_share_avg": round(float(np.mean(upper_wicks / bar_ranges)), 4),
        f"{prefix}_lower_wick_share_avg": round(float(np.mean(lower_wicks / bar_ranges)), 4),
        f"{prefix}_path_efficiency": round(float(direct / max(path, 1e-12)), 4) if closes.size > 1 else np.nan,
        f"{prefix}_range_abs": round(float(np.max(highs) - np.min(lows)), 8),
        f"{prefix}_volume_median": round(float(np.nanmedian(volumes)), 4),
        f"{prefix}_quote_volume_median": round(float(np.nanmedian(quote_volume)), 4),
        f"{prefix}_volume_last_vs_first": round(float(np.nanmedian(last_chunk) / np.nanmedian(first_chunk)), 4)
        if first_chunk.size > 0 and last_chunk.size > 0 and np.nanmedian(first_chunk) > 0.0
        else np.nan,
    }
    if anchor_price is not None and np.isfinite(anchor_price) and anchor_price > 0.0:
        result[f"{prefix}_range_pct"] = round(((float(np.max(highs)) - float(np.min(lows))) / anchor_price) * 100.0, 4)
    if trade_counts is not None and trade_counts.size:
        trade_chunks = np.array_split(trade_counts, 3) if trade_counts.size >= 3 else [trade_counts]
        trade_first = trade_chunks[0] if trade_chunks else np.array([], dtype=np.float64)
        trade_last = trade_chunks[-1] if trade_chunks else np.array([], dtype=np.float64)
        result[f"{prefix}_trade_count_median"] = round(float(np.nanmedian(trade_counts)), 4)
        result[f"{prefix}_trade_count_last_vs_first"] = round(float(np.nanmedian(trade_last) / np.nanmedian(trade_first)), 4) if trade_first.size > 0 and trade_last.size > 0 and np.nanmedian(trade_first) > 0.0 else np.nan
    if oi_values is not None and oi_values.size:
        valid_oi = oi_values[np.isfinite(oi_values)]
        if valid_oi.size >= 2 and valid_oi[0] > 0.0:
            result[f"{prefix}_oi_delta_pct"] = round(((float(valid_oi[-1]) - float(valid_oi[0])) / float(valid_oi[0])) * 100.0, 4)
        else:
            result[f"{prefix}_oi_delta_pct"] = np.nan
    return result


def _resolve_pno_pattern_context(
    *,
    levels_frame: pd.DataFrame,
    entry_frame: pd.DataFrame,
    row: dict[str, object],
    signal_timestamp_ms: int | None,
) -> dict[str, object]:
    pump_start_timestamp_ms = _safe_int(row.get("pump_start_timestamp_ms"))
    active_high_timestamp_ms = _safe_int(row.get("active_high_timestamp_ms"))
    pullback_low_timestamp_ms = _safe_int(row.get("pullback_low_timestamp_ms"))
    level_first_timestamp_ms = _safe_int(row.get("level_first_local_high_timestamp_ms"))
    level_last_timestamp_ms = _safe_int(row.get("level_last_local_high_timestamp_ms"))
    level_valid_timestamp_ms = _safe_int(row.get("level_valid_timestamp_ms"))
    level = _safe_float(row.get("level"))
    active_high = _safe_float(row.get("active_high"))
    pullback_low = _safe_float(row.get("pullback_low"))
    leg_start = _safe_float(row.get("leg_start"))
    leg_size = _safe_float(row.get("leg_size"))
    result: dict[str, object] = {}

    levels_step_ms = _infer_pno_frame_step_ms(levels_frame, default_ms=5 * 60_000)
    entry_step_ms = _infer_pno_frame_step_ms(entry_frame, default_ms=60_000)
    signal_ts = signal_timestamp_ms or level_valid_timestamp_ms or _safe_int(row.get("timestamp_ms"))

    if signal_ts is not None:
        signal_dt = pd.Timestamp(signal_ts, unit="ms", tz="UTC")
        result["signal_hour_utc"] = int(signal_dt.hour)
        result["signal_weekday_utc"] = int(signal_dt.weekday())
        result["signal_session_bucket"] = _resolve_pno_session_bucket(signal_ts)
    if pump_start_timestamp_ms is not None and signal_ts is not None:
        result["pump_to_signal_minutes"] = round((signal_ts - pump_start_timestamp_ms) / 60_000.0, 2)
    if active_high_timestamp_ms is not None and signal_ts is not None:
        result["active_high_to_signal_minutes"] = round((signal_ts - active_high_timestamp_ms) / 60_000.0, 2)

    if pump_start_timestamp_ms is not None:
        sleep_window = _slice_pno_frame_by_timestamp(
            levels_frame,
            start_timestamp_ms=max(0, pump_start_timestamp_ms - (_PNO_TRADE_SLEEP_LOOKBACK_BARS * levels_step_ms)),
            end_timestamp_ms=max(0, pump_start_timestamp_ms - levels_step_ms),
        )
        sleep_profile = _resolve_pno_window_profile(
            sleep_window,
            prefix="sleep",
            anchor_price=leg_start or _safe_float(row.get("active_high")) or 1.0,
        )
        result.update(sleep_profile)
        if leg_size is not None and leg_start is not None and leg_start > 0.0:
            sleep_range_pct = _safe_float(sleep_profile.get("sleep_range_pct"))
            result["sleep_compression_vs_pump"] = round(float(sleep_range_pct / max((leg_size / leg_start) * 100.0, 1e-12)), 4) if sleep_range_pct is not None else np.nan

        prior_6h_window = _slice_pno_frame_by_timestamp(
            levels_frame,
            start_timestamp_ms=max(0, pump_start_timestamp_ms - 6 * 60 * 60_000),
            end_timestamp_ms=max(0, pump_start_timestamp_ms - levels_step_ms),
        )
        if not prior_6h_window.empty:
            prior_ranges = (prior_6h_window["high"] - prior_6h_window["low"]).to_numpy(dtype=np.float64, copy=False)
            prior_volumes = prior_6h_window["volume"].to_numpy(dtype=np.float64, copy=False)
            range_threshold = float(np.nanmedian(prior_ranges)) * 2.0 if prior_ranges.size else np.nan
            volume_threshold = float(np.nanmedian(prior_volumes)) * 2.0 if prior_volumes.size else np.nan
            if np.isfinite(range_threshold) and np.isfinite(volume_threshold):
                impulse_mask = (prior_ranges >= range_threshold) & (prior_volumes >= volume_threshold)
                result["prior_6h_impulse_count"] = int(np.sum(impulse_mask))

    if pump_start_timestamp_ms is not None and active_high_timestamp_ms is not None:
        pump_window = _slice_pno_frame_by_timestamp(
            levels_frame,
            start_timestamp_ms=pump_start_timestamp_ms,
            end_timestamp_ms=active_high_timestamp_ms,
        )
        pump_profile = _resolve_pno_window_profile(
            pump_window,
            prefix="pump_shape",
            anchor_price=leg_start or active_high or 1.0,
        )
        result.update(pump_profile)
        if not pump_window.empty and leg_size is not None and leg_size > 0.0:
            opens = pump_window["open"].to_numpy(dtype=np.float64, copy=False)
            highs = pump_window["high"].to_numpy(dtype=np.float64, copy=False)
            lows = pump_window["low"].to_numpy(dtype=np.float64, copy=False)
            closes = pump_window["close"].to_numpy(dtype=np.float64, copy=False)
            green_bodies = np.maximum(closes - opens, 0.0)
            result["pump_first_bar_share_of_leg"] = round(float(green_bodies[0] / leg_size), 4)
            result["pump_best_bar_share_of_leg"] = round(float(np.max(green_bodies) / leg_size), 4)
            result["pump_last_bar_share_of_leg"] = round(float(green_bodies[-1] / leg_size), 4)
            front_idx = max(1, int(np.ceil(len(pump_window) / 3.0)))
            front_move = float(np.max(highs[:front_idx]) - np.min(lows[:front_idx])) if front_idx > 0 else 0.0
            result["pump_front_third_share_of_leg"] = round(front_move / leg_size, 4)
            running_peak = np.maximum.accumulate(highs)
            result["pump_internal_drawdown_share"] = round(float(np.max((running_peak - lows) / leg_size)), 4)

    pullback_end_ts = pullback_low_timestamp_ms
    if active_high_timestamp_ms is not None and pullback_end_ts is not None:
        pullback_window = _slice_pno_frame_by_timestamp(
            entry_frame,
            start_timestamp_ms=active_high_timestamp_ms,
            end_timestamp_ms=pullback_end_ts,
        )
        pullback_profile = _resolve_pno_window_profile(
            pullback_window,
            prefix="pullback_shape",
            anchor_price=active_high or 1.0,
        )
        result.update(pullback_profile)
        if not pullback_window.empty:
            opens = pullback_window["open"].to_numpy(dtype=np.float64, copy=False)
            closes = pullback_window["close"].to_numpy(dtype=np.float64, copy=False)
            red_bodies = np.maximum(opens - closes, 0.0)
            red_bodies = red_bodies[red_bodies > 0.0]
            if red_bodies.size >= 1:
                result["pullback_first_red_body"] = round(float(red_bodies[0]), 8)
            if red_bodies.size >= 2:
                result["pullback_second_red_body"] = round(float(red_bodies[1]), 8)
                result["pullback_first_second_red_ratio"] = round(float(red_bodies[0] / max(red_bodies[1], 1e-12)), 4)
            if {"ema9", "ema20"}.issubset(pullback_window.columns):
                lows = pullback_window["low"].to_numpy(dtype=np.float64, copy=False)
                highs = pullback_window["high"].to_numpy(dtype=np.float64, copy=False)
                ema9 = pullback_window["ema9"].to_numpy(dtype=np.float64, copy=False)
                ema20 = pullback_window["ema20"].to_numpy(dtype=np.float64, copy=False)
                result["pullback_close_below_ema9_share"] = round(float(np.mean(closes < ema9)), 4)
                result["pullback_close_below_ema20_share"] = round(float(np.mean(closes < ema20)), 4)
                result["pullback_touch_ema9_count"] = int(np.sum((lows <= ema9) & (highs >= ema9)))
                result["pullback_touch_ema20_count"] = int(np.sum((lows <= ema20) & (highs >= ema20)))

    if pullback_low_timestamp_ms is not None and signal_ts is not None and signal_ts >= pullback_low_timestamp_ms:
        rebound_window = _slice_pno_frame_by_timestamp(
            entry_frame,
            start_timestamp_ms=pullback_low_timestamp_ms,
            end_timestamp_ms=signal_ts,
        )
        rebound_profile = _resolve_pno_window_profile(
            rebound_window,
            prefix="rebound",
            anchor_price=pullback_low or 1.0,
        )
        result.update(rebound_profile)
        if not rebound_window.empty and pullback_low is not None and pullback_low > 0.0:
            result["rebound_gain_pct"] = round(((float(rebound_window["high"].max()) - pullback_low) / pullback_low) * 100.0, 4)

    if level is not None and signal_ts is not None:
        level_start_ts = level_first_timestamp_ms or level_valid_timestamp_ms
        level_window = _slice_pno_frame_by_timestamp(
            entry_frame,
            start_timestamp_ms=level_start_ts,
            end_timestamp_ms=signal_ts,
        )
        if not level_window.empty:
            level_timestamps = level_window["timestamp"].to_numpy(dtype=np.int64, copy=False)
            highs = level_window["high"].to_numpy(dtype=np.float64, copy=False)
            closes = level_window["close"].to_numpy(dtype=np.float64, copy=False)
            result["level_type"] = "single_touch" if int(_safe_int(row.get("touches")) or 0) <= 1 else "cluster"
            result["level_life_bars"] = int(len(level_window))
            result["level_life_minutes"] = round((int(level_timestamps[-1]) - int(level_timestamps[0])) / 60_000.0, 2)
            result["level_cluster_span_bars"] = int(max(0, round(((level_last_timestamp_ms or signal_ts) - (level_first_timestamp_ms or signal_ts)) / max(entry_step_ms, 1))))
            result["level_false_break_wick_count"] = int(np.sum((highs > level) & (closes <= level)))
            result["level_close_above_count_before_signal"] = int(np.sum(closes > level))
            result["level_respect_bars"] = int(np.sum(highs <= level))
        if active_high is not None and active_high > 0.0:
            result["level_distance_to_active_high_pct"] = round(((active_high - level) / active_high) * 100.0, 4)
        if pullback_low is not None and level > 0.0:
            result["level_distance_to_pullback_low_pct"] = round(((level - pullback_low) / level) * 100.0, 4)
        if pump_start_timestamp_ms is not None:
            supply_24h_window = _slice_pno_frame_by_timestamp(
                levels_frame,
                start_timestamp_ms=max(0, pump_start_timestamp_ms - 24 * 60 * 60_000),
                end_timestamp_ms=max(0, pump_start_timestamp_ms - levels_step_ms),
            )
            if not supply_24h_window.empty:
                highs = supply_24h_window["high"].to_numpy(dtype=np.float64, copy=False)
                result["left_supply_bars_above_level_24h"] = int(np.sum(highs >= level))
                if active_high is not None:
                    result["left_supply_bars_above_active_high_24h"] = int(np.sum(highs >= active_high))

    return result


def _build_pno_research_context_row(
    row: dict[str, object],
    *,
    source_stage: str,
    source_status: str,
    source_reason: str,
    signal_context: dict[str, object] | None = None,
    pattern_context: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = dict(row)
    payload["source_stage"] = source_stage
    payload["source_status"] = source_status
    payload["source_reason"] = source_reason
    if signal_context:
        payload.update(signal_context)
    if pattern_context:
        payload.update(pattern_context)

    leg_start = _safe_float(payload.get("leg_start"))
    leg_size = _safe_float(payload.get("leg_size"))
    active_high = _safe_float(payload.get("active_high"))
    pullback_low = _safe_float(payload.get("pullback_low"))
    pullback_depth = _safe_float(payload.get("pullback_depth"))
    level = _safe_float(payload.get("level"))
    entry_pos = _safe_float(payload.get("entry_pos"))
    level_maturity = _safe_float(payload.get("level_maturity_fraction"))
    score = _safe_float(payload.get("final_score")) or _safe_float(payload.get("score"))
    span = (active_high - pullback_low) if active_high is not None and pullback_low is not None else None
    level_pos = entry_pos
    if level_pos is None and level is not None and span is not None and span > 0.0:
        level_pos = (level - pullback_low) / span

    payload["stage_key"] = _resolve_pno_stage_key_from_row(payload)
    payload["pump_leg_pct"] = round((leg_size / leg_start) * 100.0, 4) if leg_size is not None and leg_start is not None and leg_start > 0.0 else np.nan
    payload["pullback_fraction_of_leg"] = round(pullback_depth / leg_size, 4) if pullback_depth is not None and leg_size is not None and leg_size > 0.0 else np.nan
    payload["level_fraction_of_pullback"] = round(level_pos, 4) if level_pos is not None and np.isfinite(level_pos) else np.nan
    payload["stop_distance_pct"] = round(((_safe_float(payload.get("entry_plan")) or level or 0.0) - (_safe_float(payload.get("sl_plan")) or np.nan)) / (_safe_float(payload.get("entry_plan")) or level or np.nan) * 100.0, 4) if (_safe_float(payload.get("entry_plan")) or level) not in {None, 0.0} and _safe_float(payload.get("sl_plan")) is not None else np.nan
    payload["tp2_distance_pct"] = round(((_safe_float(payload.get("tp2")) or np.nan) - (_safe_float(payload.get("entry_plan")) or level or np.nan)) / (_safe_float(payload.get("entry_plan")) or level or np.nan) * 100.0, 4) if (_safe_float(payload.get("entry_plan")) or level) not in {None, 0.0} and _safe_float(payload.get("tp2")) is not None else np.nan
    payload["pump_impulse_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pump_impulse_atr_pre")), thresholds=(3.5, 5.0, 7.5), labels=("impulse_weak", "impulse_ok", "impulse_strong", "impulse_extreme"))
    payload["pump_volume_start_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pump_volume_ratio_start")), thresholds=(3.0, 6.0, 10.0), labels=("vol_start_ok", "vol_start_strong", "vol_start_hot", "vol_start_extreme"))
    payload["pump_volume_continue_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pump_volume_ratio_continue")), thresholds=(1.5, 3.0, 6.0), labels=("vol_cont_ok", "vol_cont_strong", "vol_cont_hot", "vol_cont_extreme"))
    pump_path_efficiency = _safe_float(payload.get("pump_path_efficiency"))
    pump_wick_share = _safe_float(payload.get("pump_wick_share"))
    if pump_path_efficiency is None or pump_wick_share is None:
        payload["pump_cleanliness_bucket"] = "na"
    elif pump_path_efficiency >= 0.45 and pump_wick_share <= 0.45:
        payload["pump_cleanliness_bucket"] = "clean"
    elif pump_path_efficiency >= 0.30 and pump_wick_share <= 0.60:
        payload["pump_cleanliness_bucket"] = "acceptable"
    else:
        payload["pump_cleanliness_bucket"] = "dirty"
    payload["pump_vs_pre_2h_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pump_vs_pre_2h_ratio")), thresholds=(1.5, 2.5, 4.0), labels=("pretrend_small", "pretrend_ok", "pretrend_strong", "pretrend_dominant"))
    payload["pre_pump_ema_cross_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pre_pump_ema_crosses_1h")), thresholds=(1.0, 2.0), labels=("ema_cross_1", "ema_cross_2", "ema_cross_3plus"))
    payload["pullback_depth_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pullback_fraction_of_leg")), thresholds=(0.25, 0.40, 0.55), labels=("pb_shallow", "pb_balanced", "pb_deep", "pb_very_deep"))
    payload["pullback_age_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pullback_age_bars")), thresholds=(3.0, 6.0, 10.0), labels=("pb_fast", "pb_normal", "pb_slow", "pb_stale"))
    payload["level_maturity_bucket"] = _bucketize_pno_value(level_maturity, thresholds=(0.25, 0.50, 0.75), labels=("lvl_young", "lvl_working", "lvl_mature", "lvl_old"))
    touches_value = _safe_int(payload.get("touches"))
    payload["touches_bucket"] = "na" if touches_value is None else ("touch_1" if touches_value <= 1 else "touch_2" if touches_value == 2 else "touch_3plus")
    payload["entry_zone_bucket"] = _bucketize_pno_value(level_pos, thresholds=(0.33, 0.50, 0.60), labels=("zone_low", "zone_midlow", "zone_upper_ok", "zone_high"))
    payload["score_bucket"] = _bucketize_pno_value(score, thresholds=(70.0, 80.0, 90.0), labels=("score_borderline", "score_ok", "score_strong", "score_elite"))
    payload["signal_body_bucket"] = _bucketize_pno_value(_safe_float(payload.get("signal_bar_body_share")), thresholds=(0.25, 0.50, 0.75), labels=("signal_small", "signal_medium", "signal_strong", "signal_expansion"))
    payload["signal_volume_bucket"] = _bucketize_pno_value(_safe_float(payload.get("signal_bar_volume_vs_recent")), thresholds=(1.0, 2.0, 4.0), labels=("signal_vol_flat", "signal_vol_ok", "signal_vol_strong", "signal_vol_spike"))
    payload["signal_close_clearance_bucket"] = _bucketize_pno_value(_safe_float(payload.get("signal_close_clearance_pct")), thresholds=(0.0, 0.10, 0.40), labels=("signal_close_below", "signal_close_flat", "signal_close_clear", "signal_close_expand"))
    payload["sleep_compression_bucket"] = _bucketize_pno_value(_safe_float(payload.get("sleep_compression_vs_pump")), thresholds=(0.15, 0.30, 0.50), labels=("sleep_tight", "sleep_ok", "sleep_loose", "sleep_noisy"))
    payload["pump_shape_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pump_shape_positive_close_share")), thresholds=(0.55, 0.70, 0.85), labels=("pump_noisy", "pump_mixed", "pump_orderly", "pump_persistent"))
    payload["pump_drawdown_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pump_internal_drawdown_share")), thresholds=(0.10, 0.20, 0.35), labels=("pump_tight", "pump_ok", "pump_loose", "pump_dirty"))
    payload["pullback_ema_hold_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pullback_close_below_ema9_share")), thresholds=(0.0, 0.20, 0.50), labels=("pb_holds_ema9", "pb_small_break", "pb_mixed", "pb_weak"))
    payload["level_life_bucket"] = _bucketize_pno_value(_safe_float(payload.get("level_life_bars")), thresholds=(3.0, 8.0, 15.0), labels=("level_fresh", "level_worked", "level_lived", "level_old"))
    payload["level_false_break_bucket"] = _bucketize_pno_value(_safe_float(payload.get("level_false_break_wick_count")), thresholds=(0.0, 1.0, 2.0), labels=("lvl_clean", "lvl_one_probe", "lvl_two_probes", "lvl_many_probes"))
    payload["signal_ema_spread_bucket"] = _bucketize_pno_value(_safe_float(payload.get("signal_bar_ema_spread_pct")), thresholds=(0.15, 0.40, 0.80), labels=("ema_tight", "ema_ok", "ema_open", "ema_extended"))
    payload["signal_followthrough_bucket"] = _bucketize_pno_value(_safe_float(payload.get("signal_followthrough_3bar_pct")), thresholds=(0.1, 0.4, 1.0), labels=("ft_flat", "ft_ok", "ft_strong", "ft_explosive"))
    payload["prior_impulse_bucket"] = _bucketize_pno_value(_safe_float(payload.get("prior_6h_impulse_count")), thresholds=(0.0, 1.0, 2.0), labels=("prior_clean", "prior_one", "prior_two", "prior_many"))
    payload["overhead_supply_bucket"] = _bucketize_pno_value(_safe_float(payload.get("left_supply_bars_above_level_24h")), thresholds=(0.0, 3.0, 10.0), labels=("supply_clear", "supply_light", "supply_medium", "supply_heavy"))
    payload["hold_status_group"] = str(payload.get("hold_status_at_validation") or payload.get("hold_status_at_level_search") or "na")
    payload["leg_start_status_group"] = str(payload.get("leg_start_status_at_validation") or "na")
    return payload


def _summarize_pno_feature_buckets(frame: pd.DataFrame, *, scope: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    feature_columns = [
        "pump_impulse_bucket",
        "pump_volume_start_bucket",
        "pump_volume_continue_bucket",
        "pump_cleanliness_bucket",
        "pump_vs_pre_2h_bucket",
        "sleep_compression_bucket",
        "pump_shape_bucket",
        "pump_drawdown_bucket",
        "pre_pump_ema_cross_bucket",
        "pullback_depth_bucket",
        "pullback_age_bucket",
        "pullback_ema_hold_bucket",
        "level_maturity_bucket",
        "touches_bucket",
        "entry_zone_bucket",
        "level_life_bucket",
        "level_false_break_bucket",
        "score_bucket",
        "signal_body_bucket",
        "signal_volume_bucket",
        "signal_close_clearance_bucket",
        "signal_ema_spread_bucket",
        "signal_followthrough_bucket",
        "prior_impulse_bucket",
        "overhead_supply_bucket",
        "hold_status_group",
        "leg_start_status_group",
    ]
    summary_rows: list[dict[str, object]] = []
    total_count = len(frame)
    for feature in feature_columns:
        if feature not in frame.columns:
            continue
        scoped = frame.loc[frame[feature].notna()].copy()
        if scoped.empty:
            continue
        for bucket_value, group in scoped.groupby(feature, dropna=False):
            trade_group = group.loc[group.get("is_trade", False).astype(bool)] if "is_trade" in group.columns else pd.DataFrame()
            summary_rows.append(
                {
                    "scope": scope,
                    "feature": feature,
                    "bucket": bucket_value,
                    "count": int(len(group)),
                    "share": round(len(group) / total_count, 4) if total_count > 0 else np.nan,
                    "triggered_rate": round(float(group["is_triggered"].mean()), 4) if "is_triggered" in group.columns else np.nan,
                    "trade_count": int(len(trade_group)) if not trade_group.empty else 0,
                    "win_rate": round(float(trade_group["is_win"].mean()), 4) if not trade_group.empty and "is_win" in trade_group.columns else np.nan,
                    "tp2_rate": round(float((trade_group["stage5_outcome"] == "tp2").mean()), 4) if not trade_group.empty and "stage5_outcome" in trade_group.columns else np.nan,
                    "mean_pnl_percent": round(float(pd.to_numeric(trade_group["pnl_percent"], errors="coerce").mean()), 4) if not trade_group.empty and "pnl_percent" in trade_group.columns else np.nan,
                    "median_pnl_percent": round(float(pd.to_numeric(trade_group["pnl_percent"], errors="coerce").median()), 4) if not trade_group.empty and "pnl_percent" in trade_group.columns else np.nan,
                }
            )
    return pd.DataFrame(summary_rows)


def _export_pno_research_context(
    *,
    diagnostics_dir: Path,
    symbol_frames: dict[str, SymbolMtfFrames],
    trade_rows: list[dict[str, object]],
    stage_rows_by_stage: dict[str, list[dict[str, object]]],
    stage_rejections_by_stage: dict[str, dict[str, list[dict[str, object]]]],
) -> None:
    research_dir = diagnostics_dir / "research_context"
    research_dir.mkdir(parents=True, exist_ok=True)
    prepared_levels_frames: dict[str, pd.DataFrame] = {}
    prepared_entry_frames: dict[str, pd.DataFrame] = {}

    def _get_prepared_levels_frame(symbol: str) -> pd.DataFrame:
        cached = prepared_levels_frames.get(symbol)
        if cached is not None:
            return cached
        mtf_frames = symbol_frames.get(symbol)
        if mtf_frames is None:
            cached = pd.DataFrame()
        else:
            cached = _prepare_pno_levels_plot_source(mtf_frames.levels_frame)
        prepared_levels_frames[symbol] = cached
        return cached

    def _get_prepared_entry_frame(symbol: str) -> pd.DataFrame:
        cached = prepared_entry_frames.get(symbol)
        if cached is not None:
            return cached
        mtf_frames = symbol_frames.get(symbol)
        if mtf_frames is None:
            cached = pd.DataFrame()
        else:
            cached = _prepare_pno_entry_plot_source_with_ema(
                levels_frame=mtf_frames.levels_frame,
                entry_frame=mtf_frames.entry_frame,
            )
        prepared_entry_frames[symbol] = cached
        return cached

    stage_context_rows: list[dict[str, object]] = []
    for stage_id, rows in stage_rows_by_stage.items():
        for row in rows:
            symbol = str(row.get("symbol") or "")
            signal_timestamp_ms = _safe_int(row.get("entry_signal_timestamp_ms")) or _safe_int(row.get("timestamp_ms"))
            signal_context = _resolve_pno_signal_bar_context(
                entry_frame=_get_prepared_entry_frame(symbol),
                timestamp_ms=signal_timestamp_ms,
                level=_safe_float(row.get("level")),
            )
            pattern_context = _resolve_pno_pattern_context(
                levels_frame=_get_prepared_levels_frame(symbol),
                entry_frame=_get_prepared_entry_frame(symbol),
                row=row,
                signal_timestamp_ms=signal_timestamp_ms,
            )
            stage_context_rows.append(
                _build_pno_research_context_row(
                    row,
                    source_stage=stage_id,
                    source_status="passed",
                    source_reason="passed",
                    signal_context=signal_context,
                    pattern_context=pattern_context,
                )
            )
    for stage_id, reason_groups in stage_rejections_by_stage.items():
        for reason, rows in reason_groups.items():
            for row in rows:
                symbol = str(row.get("symbol") or "")
                signal_timestamp_ms = _safe_int(row.get("entry_signal_timestamp_ms")) or _safe_int(row.get("timestamp_ms"))
                signal_context = _resolve_pno_signal_bar_context(
                    entry_frame=_get_prepared_entry_frame(symbol),
                    timestamp_ms=signal_timestamp_ms,
                    level=_safe_float(row.get("level")),
                )
                pattern_context = _resolve_pno_pattern_context(
                    levels_frame=_get_prepared_levels_frame(symbol),
                    entry_frame=_get_prepared_entry_frame(symbol),
                    row=row,
                    signal_timestamp_ms=signal_timestamp_ms,
                )
                stage_context_rows.append(
                    _build_pno_research_context_row(
                        row,
                        source_stage=stage_id,
                        source_status="rejected",
                        source_reason=reason,
                        signal_context=signal_context,
                        pattern_context=pattern_context,
                    )
                )
    stage_context_frame = pd.DataFrame(stage_context_rows)
    stage_context_frame.to_csv(research_dir / "stage_context_all.csv", index=False)

    trade_context_rows: list[dict[str, object]] = []
    stage5_outcome_by_key: dict[str, str] = {}
    for row in trade_rows:
        symbol = str(row.get("symbol") or "")
        signal_timestamp_ms = _safe_int(row.get("entry_signal_timestamp_ms")) or _safe_int(row.get("entry_timestamp_ms"))
        signal_context = _resolve_pno_signal_bar_context(
            entry_frame=_get_prepared_entry_frame(symbol),
            timestamp_ms=signal_timestamp_ms,
            level=_safe_float(row.get("level")),
        )
        pattern_context = _resolve_pno_pattern_context(
            levels_frame=_get_prepared_levels_frame(symbol),
            entry_frame=_get_prepared_entry_frame(symbol),
            row=row,
            signal_timestamp_ms=signal_timestamp_ms,
        )
        enriched = _build_pno_research_context_row(
            row,
            source_stage=PNO_STAGE_5_TRADE,
            source_status="passed",
            source_reason=str(row.get("result_type") or row.get("category") or "trade"),
            signal_context=signal_context,
            pattern_context=pattern_context,
        )
        result_type = str(row.get("result_type") or "").lower()
        enriched["stage5_outcome"] = result_type
        enriched["is_trade"] = True
        enriched["is_triggered"] = True
        enriched["is_win"] = result_type in {"tp1_be", "tp2"}
        trade_context_rows.append(enriched)
        stage_key = str(enriched.get("stage_key") or "")
        if stage_key:
            stage5_outcome_by_key[stage_key] = result_type

    trade_context_frame = pd.DataFrame(trade_context_rows)
    trade_context_frame.to_csv(research_dir / "trade_context.csv", index=False)

    stage5_candidate_rows: list[dict[str, object]] = list(trade_context_rows)
    for reason, rows in stage_rejections_by_stage.get(PNO_STAGE_5_TRADE, {}).items():
        for row in rows:
            symbol = str(row.get("symbol") or "")
            signal_timestamp_ms = _safe_int(row.get("entry_signal_timestamp_ms")) or _safe_int(row.get("timestamp_ms"))
            signal_context = _resolve_pno_signal_bar_context(
                entry_frame=_get_prepared_entry_frame(symbol),
                timestamp_ms=signal_timestamp_ms,
                level=_safe_float(row.get("level")),
            )
            pattern_context = _resolve_pno_pattern_context(
                levels_frame=_get_prepared_levels_frame(symbol),
                entry_frame=_get_prepared_entry_frame(symbol),
                row=row,
                signal_timestamp_ms=signal_timestamp_ms,
            )
            enriched = _build_pno_research_context_row(
                row,
                source_stage=PNO_STAGE_5_TRADE,
                source_status="rejected",
                source_reason=reason,
                signal_context=signal_context,
                pattern_context=pattern_context,
            )
            enriched["stage5_outcome"] = f"rejected_{reason}"
            enriched["is_trade"] = False
            enriched["is_triggered"] = False
            enriched["is_win"] = False
            stage5_candidate_rows.append(enriched)
            stage_key = str(enriched.get("stage_key") or "")
            if stage_key:
                stage5_outcome_by_key.setdefault(stage_key, f"rejected_{reason}")

    stage5_candidate_frame = pd.DataFrame(stage5_candidate_rows)
    stage5_candidate_frame.to_csv(research_dir / "stage5_trigger_context.csv", index=False)

    stage4_context_rows: list[dict[str, object]] = []
    for row in stage_rows_by_stage.get(PNO_STAGE_4_LEVEL, []):
        symbol = str(row.get("symbol") or "")
        enriched = _build_pno_research_context_row(
            row,
            source_stage=PNO_STAGE_4_LEVEL,
            source_status="passed",
            source_reason="passed",
            pattern_context=_resolve_pno_pattern_context(
                levels_frame=_get_prepared_levels_frame(symbol),
                entry_frame=_get_prepared_entry_frame(symbol),
                row=row,
                signal_timestamp_ms=_safe_int(row.get("entry_signal_timestamp_ms")) or _safe_int(row.get("timestamp_ms")),
            ),
        )
        downstream_outcome = stage5_outcome_by_key.get(str(enriched.get("stage_key") or ""), "not_reached_stage5")
        enriched["downstream_stage5_outcome"] = downstream_outcome
        enriched["downstream_triggered"] = downstream_outcome in {"sl", "tp1_be", "tp2"}
        enriched["downstream_win"] = downstream_outcome in {"tp1_be", "tp2"}
        stage4_context_rows.append(enriched)
    for reason, rows in stage_rejections_by_stage.get(PNO_STAGE_4_LEVEL, {}).items():
        for row in rows:
            symbol = str(row.get("symbol") or "")
            enriched = _build_pno_research_context_row(
                row,
                source_stage=PNO_STAGE_4_LEVEL,
                source_status="rejected",
                source_reason=reason,
                pattern_context=_resolve_pno_pattern_context(
                    levels_frame=_get_prepared_levels_frame(symbol),
                    entry_frame=_get_prepared_entry_frame(symbol),
                    row=row,
                    signal_timestamp_ms=_safe_int(row.get("entry_signal_timestamp_ms")) or _safe_int(row.get("timestamp_ms")),
                ),
            )
            enriched["downstream_stage5_outcome"] = f"rejected_{reason}"
            enriched["downstream_triggered"] = False
            enriched["downstream_win"] = False
            stage4_context_rows.append(enriched)
    stage4_context_frame = pd.DataFrame(stage4_context_rows)
    stage4_context_frame.to_csv(research_dir / "stage4_level_context.csv", index=False)

    summary_frames = [
        _summarize_pno_feature_buckets(trade_context_frame, scope="trades"),
        _summarize_pno_feature_buckets(stage5_candidate_frame, scope="stage5"),
        _summarize_pno_feature_buckets(stage4_context_frame.assign(is_trade=stage4_context_frame.get("downstream_triggered", False), is_triggered=stage4_context_frame.get("downstream_triggered", False), is_win=stage4_context_frame.get("downstream_win", False), stage5_outcome=stage4_context_frame.get("downstream_stage5_outcome", pd.Series(dtype=object))), scope="stage4"),
    ]
    summary_frame = pd.concat([frame for frame in summary_frames if not frame.empty], ignore_index=True) if any(not frame.empty for frame in summary_frames) else pd.DataFrame()
    summary_frame.to_csv(research_dir / "feature_summary.csv", index=False)

    stage_reason_summary_rows: list[dict[str, object]] = []
    for stage_id, rows in stage_rows_by_stage.items():
        stage_reason_summary_rows.append({"stage_id": stage_id, "status": "passed", "reason": "passed", "count": int(len(rows))})
    for stage_id, reason_groups in stage_rejections_by_stage.items():
        for reason, rows in reason_groups.items():
            stage_reason_summary_rows.append({"stage_id": stage_id, "status": "rejected", "reason": reason, "count": int(len(rows))})
    pd.DataFrame(stage_reason_summary_rows).to_csv(research_dir / "stage_reason_summary.csv", index=False)


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
    selected_stage_ids = _resolve_ppa_stage_ids(args)

    symbols_with_trades = 0
    symbols_with_stage_events = 0
    total_trades_generated = 0
    total_stage_events = 0
    stage_rows_by_stage: dict[str, list[dict[str, object]]] = {
        stage_id: []
        for stage_id in selected_stage_ids
    }

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

        stage_events_raw = diagnostics.get("stage_events", [])
        symbol_stage_events = 0
        if isinstance(stage_events_raw, list):
            for raw_event in stage_events_raw:
                if not isinstance(raw_event, dict):
                    continue
                stage_id = raw_event.get("stage_id")
                if not isinstance(stage_id, str) or stage_id not in stage_rows_by_stage:
                    continue
                symbol_stage_events += 1
                stage_rows_by_stage[stage_id].append(
                    {
                        "symbol": symbol,
                        **raw_event,
                    }
                )
        total_stage_events += symbol_stage_events
        if symbol_stage_events > 0:
            symbols_with_stage_events += 1

        payload = {
            "symbol": symbol,
            "trades_generated": len(trade_rows),
            "diagnostics": diagnostics,
            "trades": trade_rows,
        }
        base_name = symbol.replace("/", "_")
        (diagnostics_dir / f"{base_name}_diagnostics.json").write_text(
            _to_compact_json(payload),
            encoding="utf-8",
        )
        if trade_rows:
            pd.DataFrame(trade_rows).to_csv(diagnostics_dir / f"{base_name}_trades.csv", index=False)

    _export_ppa_stage_reviews(
        diagnostics_dir=diagnostics_dir,
        stage_rows_by_stage=stage_rows_by_stage,
        selected_stage_ids=selected_stage_ids,
    )

    logger.info(
        "%s: сохранена диагностика post_pump_absorption stage_symbols=%s stage_events=%s trades_generated=%s stages=%s output_dir=%s",
        log_prefix,
        symbols_with_stage_events,
        total_stage_events,
        total_trades_generated,
        ",".join(selected_stage_ids),
        diagnostics_dir,
    )
    if total_stage_events == 0:
        logger.warning("%s: не найдено событий post_pump_absorption для выбранных stages", log_prefix)


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
        output_path.write_text(_to_compact_json(payload), encoding="utf-8")

    logger.info(
        "%s: сохранена диагностическая визуализация bee_bite symbols=%s trades_generated=%s output_dir=%s",
        log_prefix,
        symbols_with_states,
        total_trades_generated,
        diagnostics_dir,
    )
    if symbols_with_states == 0:
        logger.warning("%s: не найдено диагностических данных bee_bite для визуализации", log_prefix)


def _plot_pno_diagnostics_for_symbols(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
    strategy: PnoStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    params_row: pd.Series,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    log_prefix: str,
) -> None:
    output_dir = Path(getattr(args, "output_dir", None) or (config.backtest.results_dir / "trade_plots"))
    diagnostics_dir = output_dir / "pno_diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = diagnostics_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    selected_stage_ids = _resolve_pno_stage_ids(args)
    selected_stage_id_set = set(selected_stage_ids)

    symbols_with_trades = 0
    symbols_with_stage_events = 0
    total_trades_generated = 0
    total_stage_events = 0
    total_charts_generated = 0
    all_trade_rows: list[dict[str, object]] = []
    total_symbols = len(symbol_frames)
    symbol_render_start_time = time.monotonic()
    stage_rows_by_stage: dict[str, list[dict[str, object]]] = {
        stage_id: []
        for stage_id in PNO_STAGE_SEQUENCE
    }
    stage_rejections_by_stage: dict[str, dict[str, list[dict[str, object]]]] = {
        stage_id: {}
        for stage_id in PNO_STAGE_SEQUENCE
    }

    pno_params_template = _build_pno_params_template_from_row(
        params_row,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
    )
    logger.info(
        "%s: pno plot export start symbols=%s stages=%s",
        log_prefix,
        total_symbols,
        ",".join(selected_stage_ids),
    )
    for symbol_index, (symbol, mtf_frames) in enumerate(symbol_frames.items(), start=1):
        params = replace(pno_params_template, symbol=symbol)
        trades = strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)
        diagnostics = strategy.consume_last_generation_diagnostics()
        trade_rows = [_flatten_trade_for_diagnostics(trade) for trade in trades]
        all_trade_rows.extend(trade_rows)
        total_trades_generated += len(trade_rows)
        if trade_rows:
            symbols_with_trades += 1

        stage_events_raw = diagnostics.get("stage_events", [])
        symbol_stage_events = 0
        if isinstance(stage_events_raw, list):
            for raw_event in stage_events_raw:
                if not isinstance(raw_event, dict):
                    continue
                stage_id = raw_event.get("stage_id")
                if not isinstance(stage_id, str) or stage_id not in stage_rows_by_stage:
                    continue
                if stage_id in selected_stage_id_set:
                    symbol_stage_events += 1
                stage_rows_by_stage[stage_id].append(
                    {
                        "symbol": symbol,
                        **raw_event,
                    }
                )
        total_stage_events += symbol_stage_events
        if symbol_stage_events > 0:
            symbols_with_stage_events += 1

        stage_rejections_raw = diagnostics.get("stage_rejections", [])
        symbol_stage_rejections = 0
        if isinstance(stage_rejections_raw, list):
            for raw_rejection in stage_rejections_raw:
                if not isinstance(raw_rejection, dict):
                    continue
                stage_id = raw_rejection.get("stage_id")
                if not isinstance(stage_id, str) or stage_id not in stage_rejections_by_stage:
                    continue
                if stage_id in selected_stage_id_set:
                    symbol_stage_rejections += 1
                reason = str(raw_rejection.get("reason") or "unknown")
                stage_rejections_by_stage[stage_id].setdefault(reason, []).append(
                    {
                        "symbol": symbol,
                        **raw_rejection,
                    }
                )

        if not trade_rows and symbol_stage_events == 0 and symbol_stage_rejections == 0:
            continue

        payload = {
            "symbol": symbol,
            "trades_generated": len(trade_rows),
            "diagnostics": diagnostics,
            "trades": trade_rows,
        }
        chart_paths = _render_pno_trade_charts_for_symbol(
            charts_dir=charts_dir,
            symbol=symbol,
            mtf_frames=mtf_frames,
            trade_rows=trade_rows,
        )
        total_charts_generated += len(chart_paths)
        payload["chart_paths"] = chart_paths
        base_name = symbol.replace("/", "_")
        (diagnostics_dir / f"{base_name}_diagnostics.json").write_text(
            _to_compact_json(payload),
            encoding="utf-8",
        )
        if trade_rows:
            pd.DataFrame(trade_rows).to_csv(diagnostics_dir / f"{base_name}_trades.csv", index=False)

        if symbol_index == total_symbols or symbol_index % 25 == 0:
            elapsed = max(time.monotonic() - symbol_render_start_time, 1e-9)
            rate = symbol_index / elapsed
            remaining = total_symbols - symbol_index
            eta_seconds = remaining / rate if rate > 0.0 else None
            logger.info(
                "%s: pno plot export symbols %s/%s (%.1f%%) charts=%s stage_events=%s trades=%s eta=%s",
                log_prefix,
                symbol_index,
                total_symbols,
                (symbol_index / max(total_symbols, 1)) * 100.0,
                total_charts_generated,
                total_stage_events,
                total_trades_generated,
                _format_eta_compact(eta_seconds),
            )

    _export_pno_stage_reviews(
        diagnostics_dir=diagnostics_dir,
        symbol_frames=symbol_frames,
        stage_rows_by_stage=stage_rows_by_stage,
        stage_rejections_by_stage=stage_rejections_by_stage,
        selected_stage_ids=selected_stage_ids,
        logger=logger,
        log_prefix=log_prefix,
    )
    _export_pno_research_context(
        diagnostics_dir=diagnostics_dir,
        symbol_frames=symbol_frames,
        trade_rows=all_trade_rows,
        stage_rows_by_stage=stage_rows_by_stage,
        stage_rejections_by_stage=stage_rejections_by_stage,
    )

    logger.info(
        "%s: сохранена диагностика pno stage_symbols=%s stage_events=%s trades_generated=%s charts_generated=%s stages=%s output_dir=%s",
        log_prefix,
        symbols_with_stage_events,
        total_stage_events,
        total_trades_generated,
        total_charts_generated,
        ",".join(selected_stage_ids),
        diagnostics_dir,
    )
    if total_stage_events == 0 and symbols_with_trades == 0:
        logger.warning("%s: не найдено событий pno и не сгенерировано сделок для визуализации", log_prefix)


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

    if strategy_id == "pno":
        if not isinstance(strategy, PnoStrategy):
            logger.error("%s: unsupported visualization type for pno", log_prefix)
            return False
        _plot_pno_diagnostics_for_symbols(
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


def _select_ppa_plot_params_row_by_stage(
    *,
    args: argparse.Namespace,
    strategy: PostPumpAbsorptionStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    results: pd.DataFrame,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    logger: Logger,
) -> pd.Series | None:
    if results.empty or not symbol_frames:
        return None

    selected_stage_ids = set(_resolve_ppa_stage_ids(args))
    selected_stage_columns = [_ppa_stage_metric_column_name(stage_id) for stage_id in selected_stage_ids]
    if selected_stage_columns and all(column in results.columns for column in selected_stage_columns):
        scored_rows: list[tuple[int, int, float, int, pd.Series]] = []
        for row_index, (_, row) in enumerate(results.iterrows()):
            stage_events_count = 0
            for column in selected_stage_columns:
                raw_value = pd.to_numeric(row.get(column, 0), errors="coerce")
                if not pd.isna(raw_value):
                    stage_events_count += int(raw_value)
            raw_trades_count = pd.to_numeric(row.get("trades_count", 0), errors="coerce")
            stage_events_count = int(
                stage_events_count
            )
            trades_generated = int(raw_trades_count) if not pd.isna(raw_trades_count) else 0
            profit_factor = float(row.get("profit_factor", 0.0) or 0.0)
            scored_rows.append((stage_events_count, trades_generated, profit_factor, -row_index, row))

        if not scored_rows:
            return None

        best_score = max(scored_rows, key=lambda item: item[:4])
        logger.info(
            "запуск-бэктеста: stage-plot selected row from results stage_events=%s trades_generated=%s profit_factor=%.4f",
            best_score[0],
            best_score[1],
            best_score[2],
        )
        return best_score[4]

    scored_rows: list[tuple[int, int, float, int, pd.Series]] = []

    for row_index, (_, row) in enumerate(results.iterrows()):
        stage_events_count = 0
        trades_generated = 0

        for symbol, mtf_frames in symbol_frames.items():
            params = _build_post_pump_absorption_params_from_row(
                row,
                symbol=symbol,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
            )
            strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)
            diagnostics = strategy.consume_last_generation_diagnostics()

            stage_events = diagnostics.get("stage_events", [])
            if isinstance(stage_events, list):
                stage_events_count += sum(
                    1
                    for event in stage_events
                    if isinstance(event, dict) and event.get("stage_id") in selected_stage_ids
                )
            trades_generated += int(diagnostics.get("trades_generated", 0) or 0)

        profit_factor = float(row.get("profit_factor", 0.0) or 0.0)
        scored_rows.append((stage_events_count, trades_generated, profit_factor, -row_index, row))

    if not scored_rows:
        return None

    best_score = max(scored_rows, key=lambda item: item[:4])
    logger.info(
        "запуск-бэктеста: stage-plot selected row by stage_events=%s trades_generated=%s profit_factor=%.4f",
        best_score[0],
        best_score[1],
        best_score[2],
    )
    return best_score[4]


def _select_pno_plot_params_row_by_stage(
    *,
    args: argparse.Namespace,
    strategy: PnoStrategy,
    symbol_frames: dict[str, SymbolMtfFrames],
    results: pd.DataFrame,
    levels_timeframe: Timeframe,
    entry_timeframe: Timeframe,
    logger: Logger,
) -> pd.Series | None:
    if results.empty or not symbol_frames:
        return None

    selected_stage_ids = set(_resolve_pno_stage_ids(args))
    selected_stage_columns = [_ppa_stage_metric_column_name(stage_id) for stage_id in selected_stage_ids]
    if selected_stage_columns and all(column in results.columns for column in selected_stage_columns):
        scored_rows: list[tuple[int, int, int, float, int, pd.Series]] = []
        needs_rejection_fallback = False
        for row_index, (_, row) in enumerate(results.iterrows()):
            stage_events_count = 0
            for column in selected_stage_columns:
                raw_value = pd.to_numeric(row.get(column, 0), errors="coerce")
                if not pd.isna(raw_value):
                    stage_events_count += int(raw_value)
            raw_trades_count = pd.to_numeric(row.get("trades_count", 0), errors="coerce")
            trades_generated = int(raw_trades_count) if not pd.isna(raw_trades_count) else 0
            profit_factor = float(row.get("profit_factor", 0.0) or 0.0)
            scored_rows.append((stage_events_count, 0, trades_generated, profit_factor, -row_index, row))
            if stage_events_count == 0:
                needs_rejection_fallback = True

        if not scored_rows:
            return None

        best_score = max(scored_rows, key=lambda item: item[:5])
        if best_score[0] > 0 or not needs_rejection_fallback:
            logger.info(
                "run-backtest: pno stage-plot selected row from results stage_events=%s stage_rejections=%s trades_generated=%s profit_factor=%.4f",
                best_score[0],
                best_score[1],
                best_score[2],
                best_score[3],
            )
            return best_score[5]

    scored_rows: list[tuple[int, int, int, float, int, pd.Series]] = []
    for row_index, (_, row) in enumerate(results.iterrows()):
        stage_events_count = 0
        stage_rejections_count = 0
        trades_generated = 0

        pno_params_template = _build_pno_params_template_from_row(
            row,
            levels_timeframe=levels_timeframe,
            entry_timeframe=entry_timeframe,
        )
        for symbol, mtf_frames in symbol_frames.items():
            params = replace(pno_params_template, symbol=symbol)
            strategy.generate_events_multi_tf(mtf_frames=mtf_frames, params=params)
            diagnostics = strategy.consume_last_generation_diagnostics()

            stage_events = diagnostics.get("stage_events", [])
            if isinstance(stage_events, list):
                stage_events_count += sum(
                    1
                    for event in stage_events
                    if isinstance(event, dict) and event.get("stage_id") in selected_stage_ids
                )
            stage_rejections = diagnostics.get("stage_rejections", [])
            if isinstance(stage_rejections, list):
                stage_rejections_count += sum(
                    1
                    for rejection in stage_rejections
                    if isinstance(rejection, dict) and rejection.get("stage_id") in selected_stage_ids
                )
            trades_generated += int(diagnostics.get("trades_generated", 0) or 0)

        profit_factor = float(row.get("profit_factor", 0.0) or 0.0)
        scored_rows.append((stage_events_count, stage_rejections_count, trades_generated, profit_factor, -row_index, row))

    if not scored_rows:
        return None

    best_score = max(scored_rows, key=lambda item: item[:5])
    logger.info(
        "run-backtest: pno stage-plot selected row by stage_events=%s stage_rejections=%s trades_generated=%s profit_factor=%.4f",
        best_score[0],
        best_score[1],
        best_score[2],
        best_score[3],
    )
    return best_score[5]


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
        "pno": [
            "pno_variant_id",
            "pno_deposit",
            "pno_risk_pct",
            "pno_r_trade",
            "pno_fee_rate",
            "pno_min_score",
            "pno_strong_score",
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
            'ликвидность-кэша: не удалось получить метрики биржи (%s)',
            exc,
        )

    combined_symbols = set(liquid_symbols) | exchange_liquid_symbols
    if not combined_symbols:
        fallback_symbols = exchange_symbols_normalized[:top_n]
        logger.info(
            'ликвидность-кэша: не найдено ликвидных символов, fallback на первые top_n=%s',
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
        'ликвидность-кэша: источник=cache+exchange-liquidity, символов_на_бирже=%s -> в_кэше_с_объёмом=%s -> прошло_порог_ликвидности=%s -> объединённый_кандидатный_лист=%s -> выбрано_top_n=%s',
        len(exchange_symbols_normalized),
        len(symbols_with_volume),
        len(liquid_symbols),
        len(combined_symbols),
        len(ranked_top_symbols),
    )
    logger.info(
        'ликвидность-кэша: исключено по порогу среднего объёма (min_avg_daily_volume_usd=%.2f) символов=%s',
        min_volume_usd,
        len(symbols_with_volume) - len(liquid_symbols),
    )
    logger.info(
        'ликвидность-кэша: добавлено символов по метрикам биржи=%s',
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


def _resolve_hourly_pump_timeframes(args: argparse.Namespace) -> tuple[Timeframe, ...]:
    return _resolve_timeframe_sequence(
        getattr(args, "timeframes", None),
        fallback=HOURLY_ASIA_PUMP_SUPPORTED_TIMEFRAMES,
        fallback_timeframe=Timeframe.M1,
        argument_name="--timeframes",
        supported=set(HOURLY_ASIA_PUMP_SUPPORTED_TIMEFRAMES),
    )


def _resolve_backtest_timeframes(
    *,
    strategy_id: str,
    args: argparse.Namespace,
    configured_levels_timeframe: Timeframe,
    configured_entry_timeframe: Timeframe,
) -> tuple[Timeframe, Timeframe]:
    if strategy_id not in {"post_pump_absorption", "pno"}:
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

    if strategy_id == "post_pump_absorption":
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

        levels_fallback = (
            POST_PUMP_ABSORPTION_OI_SOURCE_TIMEFRAME
            if getattr(args, "levels_tf", None) is None and entry_timeframe != Timeframe.M5
            else entry_timeframe
        )
        levels_timeframe = _resolve_timeframe(
            getattr(args, "levels_tf", None),
            fallback=levels_fallback,
            argument_name="--levels-tf",
        )
        if levels_timeframe not in {entry_timeframe, POST_PUMP_ABSORPTION_OI_SOURCE_TIMEFRAME}:
            raise ValueError(
                "post_pump_absorption supports --levels-tf only as --entry-tf "
                "or auxiliary 5m OI source"
            )
        return levels_timeframe, entry_timeframe

    fallback_pair = (
        configured_levels_timeframe,
        configured_entry_timeframe,
    )
    if fallback_pair not in PNO_BACKTEST_TIMEFRAME_PAIRS:
        fallback_pair = resolve_pno_default_timeframe_pair(mode="backtest")
    entry_timeframe = _resolve_timeframe(
        getattr(args, "entry_tf", None),
        fallback=fallback_pair[1],
        argument_name="--entry-tf",
    )
    levels_timeframe = _resolve_timeframe(
        getattr(args, "levels_tf", None),
        fallback=fallback_pair[0],
        argument_name="--levels-tf",
    )
    validate_pno_timeframe_pair(
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        supported_pairs=PNO_BACKTEST_TIMEFRAME_PAIRS,
        context="pno backtest",
    )
    return levels_timeframe, entry_timeframe


def _with_ppa_research_timeframe(args: argparse.Namespace, timeframe: Timeframe) -> argparse.Namespace:
    cloned = argparse.Namespace(**vars(args))
    cloned.command = "run-backtest"
    cloned.strategy = "post_pump_absorption"
    cloned.entry_tf = timeframe.value
    cloned.levels_tf = (
        timeframe.value
        if timeframe == Timeframe.M5
        else POST_PUMP_ABSORPTION_OI_SOURCE_TIMEFRAME.value
    )
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


def _with_ppa_stage_timeframe(
    args: argparse.Namespace,
    timeframe: Timeframe,
    *,
    preset_stage: int | None,
    preset_through_stage: int | None,
) -> argparse.Namespace:
    cloned = _with_ppa_research_timeframe(args, timeframe)
    cloned.command = "run-backtest"
    cloned.plot = True
    cloned.ppa_stage = preset_stage
    cloned.ppa_through_stage = preset_through_stage
    return cloned


def _with_pno_stage_args(
    args: argparse.Namespace,
    *,
    preset_stage: int | None,
    preset_through_stage: int | None,
) -> argparse.Namespace:
    cloned = argparse.Namespace(**vars(args))
    default_levels_timeframe, default_entry_timeframe = resolve_pno_default_timeframe_pair(mode="backtest")
    cloned.command = "run-backtest"
    cloned.strategy = "pno"
    cloned.entry_tf = getattr(cloned, "entry_tf", None) or default_entry_timeframe.value
    cloned.levels_tf = getattr(cloned, "levels_tf", None) or default_levels_timeframe.value
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
    cloned.ppa_profile = None
    cloned.ppa_deposit = None
    cloned.ppa_risk_pct = None
    cloned.ppa_stage = None
    cloned.ppa_through_stage = None
    cloned.pno_stage = preset_stage
    cloned.pno_through_stage = preset_through_stage
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
            "загрузка-данных: сбор кэша для TF=%s (символов=%s)",
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


def _resolve_fetch_symbol_list(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
) -> list[str]:
    _, exchange_client = _build_fetch_stack(config)
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


def _resolve_fetch_ppa_symbols(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    logger: Logger,
) -> list[str]:
    _, exchange_client = _build_fetch_stack(config)
    futures_symbols = exchange_client.get_futures_symbols()
    explicit_symbols = _resolve_explicit_symbols(
        requested_symbols=getattr(args, "symbols", None),
        futures_symbols_raw=futures_symbols,
        logger=logger,
    )
    if explicit_symbols is not None:
        return explicit_symbols

    min_volume_usd = float(args.min_volume_usd if args.min_volume_usd is not None else 1_000_000.0)
    top_n = args.top_n
    ranked = exchange_client.get_futures_symbols_with_liquidity_metrics()
    selected = [
        str(item["symbol"])
        for item in ranked
        if float(item.get("quote_volume", 0.0) or 0.0) >= min_volume_usd
        and int(item.get("trade_count_24h", 0) or 0) > 0
    ]
    if top_n is not None:
        selected = selected[:top_n]

    logger.info(
        "fetch-ppa-cache: exchange universe filtered active_usdt_perps=%s min_quote_volume_24h=%.2f selected=%s",
        len(ranked),
        min_volume_usd,
        len(selected),
    )
    return selected


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


def _fetch_ppa_cache_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("fetch-ppa-cache", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    if args.days <= 0:
        logger.error("fetch-ppa-cache: --days must be > 0")
        return 1
    if args.top_n is not None and args.top_n <= 0:
        logger.error("fetch-ppa-cache: --top-n must be > 0")
        return 1

    symbols = _resolve_fetch_ppa_symbols(config=config, args=args, logger=logger)
    if not symbols:
        logger.info("fetch-ppa-cache: no symbols resolved for cache load")
        return 0

    logger.info(
        "fetch-ppa-cache: resolved symbols=%s min_volume_usd=%.2f period_days=%s end_timestamp_ms=%s",
        len(symbols),
        float(args.min_volume_usd if args.min_volume_usd is not None else config.fetch.min_volume_usd),
        args.days,
        getattr(args, "end_timestamp_ms", None),
    )

    fetch_5m_args = _clone_fetch_args(
        args,
        symbols=symbols,
        timeframes=[Timeframe.M5.value],
        skip_open_interest=True,
    )
    fetch_micro_args = _clone_fetch_args(
        args,
        symbols=symbols,
        timeframes=[Timeframe.M1.value, Timeframe.M3.value],
        skip_open_interest=True,
    )

    logger.info("fetch-ppa-cache: phase 1/2 -> 5m OHLCV without open interest")
    code_5m = _fetch_data_inner(config, fetch_5m_args)
    logger.info("fetch-ppa-cache: phase 2/2 -> 1m/3m without open interest")
    code_micro = _fetch_data_inner(config, fetch_micro_args)

    exit_code = max(code_5m, code_micro)
    logger.info("fetch-ppa-cache: completed code_5m=%s code_micro=%s exit_code=%s", code_5m, code_micro, exit_code)
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
        "обновление-кэша: найдено фьючерсов=%s отправлено в fetch_all=%s",
        all_futures_count,
        len(symbols),
    )
    if not symbols:
        logger.info("обновление-кэша: не найдено символов для обновления")
        return 0

    start_timestamp_ms, end_timestamp_ms = _fetch_period(config, args.days, getattr(args, "end_timestamp_ms", None))
    include_open_interest = not _to_bool_flag(getattr(args, "skip_open_interest", False))
    failed_symbols: set[str] = set()
    for index, timeframe in enumerate(fetch_timeframes):
        logger.info("обновление-кэша: сбор кэша для TF=%s", timeframe.value)
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
            logger.info("обновление-кэша: cooldown after TF=%s sleep=75s", timeframe.value)
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
    elif strategy_id == "pno":
        if getattr(args, "pno_deposit", None) is not None:
            config.strategy.pno_deposit = float(args.pno_deposit)
        if getattr(args, "pno_risk_pct", None) is not None:
            config.strategy.pno_risk_pct = float(args.pno_risk_pct)
    levels_timeframe, entry_timeframe = _resolve_backtest_timeframes(
        strategy_id=strategy_id,
        args=args,
        configured_levels_timeframe=config.strategy.levels_timeframe,
        configured_entry_timeframe=config.strategy.entry_timeframe,
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

    should_plot = _to_bool_flag(getattr(args, "plot", None), default=False)
    stage_metric_ids_for_run: tuple[str, ...] | None = None
    if (
        should_plot
        and strategy_id == "post_pump_absorption"
        and (getattr(args, "ppa_stage", None) is not None or getattr(args, "ppa_through_stage", None) is not None)
    ):
        stage_metric_ids_for_run = _resolve_ppa_stage_ids(args)
    elif (
        should_plot
        and strategy_id == "pno"
        and (getattr(args, "pno_stage", None) is not None or getattr(args, "pno_through_stage", None) is not None)
    ):
        stage_metric_ids_for_run = _resolve_pno_stage_ids(args)

    results = runner.run(
        strategy,
        symbol_frames,
        levels_timeframe=levels_timeframe,
        entry_timeframe=entry_timeframe,
        stage_metric_ids=stage_metric_ids_for_run,
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

    if should_plot:
        if results.empty:
            logger.warning("запуск-бэктеста: plot=true, но результаты пустые")
            return 0

        if strategy_id == "post_pump_absorption" and (
            getattr(args, "ppa_stage", None) is not None or getattr(args, "ppa_through_stage", None) is not None
        ):
            best_row = _select_ppa_plot_params_row_by_stage(
                args=args,
                strategy=strategy,
                symbol_frames=symbol_frames,
                results=results,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                logger=logger,
            )
            if best_row is None:
                logger.warning("запуск-бэктеста: stage plot selection returned no params row")
                return 0
        elif strategy_id == "pno" and (
            getattr(args, "pno_stage", None) is not None or getattr(args, "pno_through_stage", None) is not None
        ):
            best_row = _select_pno_plot_params_row_by_stage(
                args=args,
                strategy=strategy,
                symbol_frames=symbol_frames,
                results=results,
                levels_timeframe=levels_timeframe,
                entry_timeframe=entry_timeframe,
                logger=logger,
            )
            if best_row is None:
                logger.warning("запуск-бэктеста: pno stage plot selection returned no params row")
                return 0
        else:
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
            log_prefix="запуск-бэктеста: plot=true",
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
        scoped_config = replace(
            config,
            strategy=replace(config.strategy, strategy_id="post_pump_absorption"),
            backtest=replace(config.backtest, results_dir=root_output_dir / timeframe.value),
        )
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


def _run_hourly_pump_research_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-hourly-pump-research", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    timeframes = _resolve_hourly_pump_timeframes(args)
    selection_profile = parse_hourly_asia_pump_profile_id(getattr(args, "selection_profile", None))
    asia_start_hour_utc = int(getattr(args, "asia_start_hour_utc", DEFAULT_ASIA_START_HOUR_UTC))
    asia_end_hour_utc = int(getattr(args, "asia_end_hour_utc", DEFAULT_ASIA_END_HOUR_UTC))
    trigger_minute = int(getattr(args, "trigger_minute", DEFAULT_TRIGGER_MINUTE))
    max_follow_minutes = int(getattr(args, "max_follow_minutes", DEFAULT_MAX_FOLLOW_MINUTES))
    timestamp_label = time.strftime("%Y%m%d_%H%M%S")
    root_output_dir = (
        Path(args.output_dir)
        if getattr(args, "output_dir", None)
        else Path(config.backtest.results_dir) / "research" / "hourly_asia_pump" / timestamp_label
    )
    root_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "run-hourly-pump-research: timeframes=%s output_dir=%s profile=%s asia=%s-%s trigger_minute=%s max_follow_minutes=%s",
        ",".join(timeframe.value for timeframe in timeframes),
        root_output_dir,
        selection_profile,
        asia_start_hour_utc,
        asia_end_hour_utc,
        trigger_minute,
        max_follow_minutes,
    )

    artifacts = build_hourly_asia_pump_research_artifacts(
        cache_dir=config.backtest.cache_dir,
        output_dir=root_output_dir,
        timeframes=timeframes,
        symbols=getattr(args, "symbols", None),
        top_n=getattr(args, "top_n", None),
        asia_start_hour_utc=asia_start_hour_utc,
        asia_end_hour_utc=asia_end_hour_utc,
        trigger_minute=trigger_minute,
        max_follow_minutes=max_follow_minutes,
        selection_profile=selection_profile,
        commission_rate=config.simulation.commission_rate,
        candidate_cache_dir=Path(config.backtest.results_dir) / "research_cache" / "hourly_asia_pump",
        reuse_candidate_cache=True,
        logger=logger,
    )
    logger.info(
        "run-hourly-pump-research: artifacts report=%s grid_summary=%s selected_events=%s",
        artifacts["report"],
        artifacts["grid_summary"],
        artifacts["selected_profile_events"],
    )
    return 0


def _run_hourly_pump_static_combo_analysis_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-hourly-pump-static-combo-analysis", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    base_events_path = Path(getattr(args, "base_events", ""))
    confirmed_events_path = Path(getattr(args, "confirmed_events", ""))
    if not base_events_path.exists():
        raise FileNotFoundError(f"Не найден base events csv: {base_events_path}")
    if not confirmed_events_path.exists():
        raise FileNotFoundError(f"Не найден confirmed events csv: {confirmed_events_path}")

    timestamp_label = time.strftime("%Y%m%d_%H%M%S")
    root_output_dir = (
        Path(args.output_dir)
        if getattr(args, "output_dir", None)
        else Path(config.backtest.results_dir) / "research" / "hourly_asia_pump_static_combo" / timestamp_label
    )
    root_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "run-hourly-pump-static-combo-analysis: входные_сделки=%s подтвержденные_сделки=%s output_dir=%s",
        base_events_path,
        confirmed_events_path,
        root_output_dir,
    )
    artifacts = build_hourly_asia_pump_static_combo_artifacts(
        base_events_path=base_events_path,
        confirmed_events_path=confirmed_events_path,
        output_dir=root_output_dir,
        cache_dir=config.backtest.cache_dir,
        commission_rate=config.simulation.commission_rate,
        logger=logger,
    )
    logger.info(
        "run-hourly-pump-static-combo-analysis: артефакты отчёт=%s каталог_комбинаций=%s сводка_приоритета=%s события_приоритета=%s графики=%s",
        artifacts["report"],
        artifacts["great_combo_catalog"],
        artifacts["priority_selected_summary"],
        artifacts["priority_selected_events"],
        artifacts["charts_manifest"],
    )
    return 0


def _run_hourly_pump_production_report_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-hourly-pump-production-report", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    static_combo_dir = Path(getattr(args, "static_combo_dir", ""))
    if not static_combo_dir.exists():
        raise FileNotFoundError(f"Не найдена директория static combo: {static_combo_dir}")

    output_dir = Path(args.output_dir) if getattr(args, "output_dir", None) else static_combo_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "run-hourly-pump-production-report: каталог_static_combo=%s output_dir=%s",
        static_combo_dir,
        output_dir,
    )
    artifacts = build_hourly_asia_pump_production_artifacts(
        static_combo_dir=static_combo_dir,
        output_dir=output_dir,
    )
    logger.info(
        "run-hourly-pump-production-report: артефакты отчёт=%s сводка=%s месяцы=%s отложенное_окно=%s kpi=%s kpi_пройден=%s",
        artifacts["report"],
        artifacts["production_default_summary"],
        artifacts["production_default_monthly"],
        artifacts["production_default_holdout"],
        artifacts["production_kpi_validation"],
        artifacts["gate_passed"],
    )
    return 0 if bool(artifacts["gate_passed"]) else 1


def _run_hourly_pump_unified_analysis_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-hourly-pump-unified-analysis", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    confirmed_events_path = Path(getattr(args, "confirmed_events", ""))
    if not confirmed_events_path.exists():
        raise FileNotFoundError(f"Не найден confirmed events csv: {confirmed_events_path}")

    output_dir = Path(args.output_dir) if getattr(args, "output_dir", None) else confirmed_events_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "run-hourly-pump-unified-analysis: confirmed_events=%s output_dir=%s",
        confirmed_events_path,
        output_dir,
    )
    artifacts = build_hourly_asia_pump_unified_artifacts(
        confirmed_events_path=confirmed_events_path,
        output_dir=output_dir,
        logger=logger,
    )
    logger.info(
        "run-hourly-pump-unified-analysis: артефакты отчёт=%s best=%s months=%s holdout=%s goal_passed=%s",
        artifacts["report"],
        artifacts["best_summary"],
        artifacts["best_monthly"],
        artifacts["best_holdout"],
        artifacts["goal_passed"],
    )
    return 0


def _run_hourly_pump_unified_edge_search_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-hourly-pump-unified-edge-search", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    base_events_path = Path(getattr(args, "base_events", ""))
    if not base_events_path.exists():
        raise FileNotFoundError(f"Не найден base events csv: {base_events_path}")

    output_dir = Path(args.output_dir) if getattr(args, "output_dir", None) else base_events_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "run-hourly-pump-unified-edge-search: base_events=%s output_dir=%s cache_dir=%s commission_rate=%.5f",
        base_events_path,
        output_dir,
        config.backtest.cache_dir,
        float(config.simulation.commission_rate),
    )
    artifacts = build_hourly_asia_pump_unified_edge_artifacts(
        base_events_path=base_events_path,
        output_dir=output_dir,
        cache_dir=config.backtest.cache_dir,
        commission_rate=float(config.simulation.commission_rate),
        logger=logger,
    )
    logger.info(
        "run-hourly-pump-unified-edge-search: артефакты отчёт=%s atomic=%s combos=%s best=%s monthly=%s holdout=%s goal_passed=%s distribution_goal_passed=%s",
        artifacts["report"],
        artifacts["atomic_summary"],
        artifacts["combo_summary"],
        artifacts["best_summary"],
        artifacts["best_monthly"],
        artifacts["best_holdout"],
        artifacts["goal_passed"],
        artifacts["distribution_goal_passed"],
    )
    return 0


def _run_hourly_pump_short_edge_search_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-hourly-pump-short-edge-search", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    base_events_path = Path(getattr(args, "base_events", ""))
    if not base_events_path.exists():
        raise FileNotFoundError(f"Не найден base events csv: {base_events_path}")

    output_dir = Path(args.output_dir) if getattr(args, "output_dir", None) else base_events_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "run-hourly-pump-short-edge-search: base_events=%s output_dir=%s cache_dir=%s commission_rate=%.5f",
        base_events_path,
        output_dir,
        config.backtest.cache_dir,
        float(config.simulation.commission_rate),
    )
    artifacts = build_hourly_asia_pump_short_edge_artifacts(
        base_events_path=base_events_path,
        output_dir=output_dir,
        cache_dir=config.backtest.cache_dir,
        commission_rate=float(config.simulation.commission_rate),
        logger=logger,
    )
    logger.info(
        "run-hourly-pump-short-edge-search: артефакты отчёт=%s atomic=%s best=%s months=%s holdout=%s goal_passed=%s",
        artifacts["report"],
        artifacts["atomic_summary"],
        artifacts["best_summary"],
        artifacts["best_monthly"],
        artifacts["best_holdout"],
        artifacts["goal_passed"],
    )
    return 0


def _run_hourly_pump_session_short_search_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("run-hourly-pump-session-short-search", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    session_id = str(getattr(args, "session", "") or "").strip().lower()
    if not session_id:
        raise ValueError("Не задана session")

    timestamp_label = time.strftime("%Y%m%d_%H%M%S")
    output_dir = (
        Path(args.output_dir)
        if getattr(args, "output_dir", None)
        else Path(config.backtest.results_dir) / "research" / "hourly_asia_pump_static_combo" / timestamp_label / f"{session_id}_short_session_search"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "run-hourly-pump-session-short-search: session=%s output_dir=%s cache_dir=%s commission_rate=%.5f",
        session_id,
        output_dir,
        config.backtest.cache_dir,
        float(config.simulation.commission_rate),
    )
    artifacts = build_hourly_asia_pump_session_short_edge_artifacts(
        session_id=session_id,
        output_dir=output_dir,
        cache_dir=config.backtest.cache_dir,
        commission_rate=float(config.simulation.commission_rate),
        symbols=getattr(args, "symbols", None),
        logger=logger,
    )
    logger.info(
        "run-hourly-pump-session-short-search: артефакты отчёт=%s best=%s exact=%s группы=%s динамика=%s месяцы=%s",
        artifacts["report"],
        artifacts["best_overall"],
        artifacts["best_by_exact_minute"],
        artifacts["best_by_standard_group"],
        artifacts["best_by_dynamic_combo"],
        artifacts["best_monthly"],
    )
    return 0


def _collect_ppa_stage_summary_rows(
    *,
    timeframe: Timeframe,
    scoped_results_dir: Path,
    preset_name: str,
) -> list[dict[str, object]]:
    strategy_results_dir = _resolve_results_dir_for_strategy(
        Path(scoped_results_dir),
        "post_pump_absorption",
    )
    stage_reviews_dir = strategy_results_dir / "trade_plots" / "post_pump_absorption_diagnostics" / "stage_reviews"
    manifest = _read_csv_or_empty(stage_reviews_dir / "manifest.csv")
    if manifest.empty:
        return []

    summary_rows: list[dict[str, object]] = []
    for _, row in manifest.iterrows():
        summary_rows.append(
            {
                "timeframe": timeframe.value,
                "preset": preset_name,
                "stage_id": row.get("stage_id"),
                "events_count": row.get("events_count"),
                "events_path": row.get("events_path"),
                "summary_path": row.get("summary_path"),
            }
        )
    return summary_rows


def _collect_pno_stage_summary_rows(
    *,
    scoped_results_dir: Path,
    preset_name: str,
) -> list[dict[str, object]]:
    strategy_results_dir = _resolve_results_dir_for_strategy(
        Path(scoped_results_dir),
        "pno",
    )
    stage_reviews_dir = strategy_results_dir / "trade_plots" / "pno_diagnostics" / "stage_reviews"
    manifest = _read_csv_or_empty(stage_reviews_dir / "manifest.csv")
    if manifest.empty:
        return []

    summary_rows: list[dict[str, object]] = []
    for _, row in manifest.iterrows():
        summary_rows.append(
            {
                "preset": preset_name,
                "stage_id": row.get("stage_id"),
                "events_count": row.get("events_count"),
                "events_path": row.get("events_path"),
                "summary_path": row.get("summary_path"),
            }
        )
    return summary_rows


def _run_ppa_stage_timeframe_job(
    *,
    config: AppConfig,
    args: argparse.Namespace,
    timeframe: Timeframe,
    preset_stage: int | None,
    preset_through_stage: int | None,
    preset_name: str,
    root_output_dir: Path,
) -> tuple[str, int, list[dict[str, object]]]:
    scoped_config = replace(
        config,
        strategy=replace(config.strategy, strategy_id="post_pump_absorption"),
        backtest=replace(config.backtest, results_dir=root_output_dir / timeframe.value),
    )
    scoped_args = _with_ppa_stage_timeframe(
        args,
        timeframe,
        preset_stage=preset_stage,
        preset_through_stage=preset_through_stage,
    )
    exit_code = _run_backtest_inner(scoped_config, scoped_args)
    summary_rows = _collect_ppa_stage_summary_rows(
        timeframe=timeframe,
        scoped_results_dir=Path(scoped_config.backtest.results_dir),
        preset_name=preset_name,
    )
    return timeframe.value, exit_code, summary_rows


def _run_pno_stage_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("pno-stage", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    preset_stage, preset_through_stage, preset_name = _resolve_pno_stage_preset(getattr(args, "preset", None))
    timestamp_label = time.strftime("%Y%m%d_%H%M%S")
    root_output_dir = (
        Path(args.output_dir)
        if getattr(args, "output_dir", None)
        else Path(config.backtest.results_dir) / "stage_review" / "pno" / preset_name / timestamp_label
    )
    root_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "pno-stage: preset=%s stage=%s through_stage=%s output_dir=%s",
        preset_name,
        preset_stage,
        preset_through_stage,
        root_output_dir,
    )

    scoped_config = replace(
        config,
        strategy=replace(config.strategy, strategy_id="pno"),
        backtest=replace(config.backtest, results_dir=root_output_dir),
    )
    scoped_args = _with_pno_stage_args(
        args,
        preset_stage=preset_stage,
        preset_through_stage=preset_through_stage,
    )
    exit_code = _run_backtest_inner(scoped_config, scoped_args)
    summary_rows = _collect_pno_stage_summary_rows(
        scoped_results_dir=Path(scoped_config.backtest.results_dir),
        preset_name=preset_name,
    )

    summary_path = root_output_dir / "stage_review_summary.csv"
    pd.DataFrame(summary_rows).to_csv(summary_path, index=False)
    context_path = root_output_dir / "stage_review_context.json"
    context_path.write_text(
        json.dumps(
            {
                "preset": preset_name,
                "stage": preset_stage,
                "through_stage": preset_through_stage,
                "symbols": list(getattr(args, "symbols", None) or []),
                "top_n": getattr(args, "top_n", None),
                "entry_timeframe": scoped_args.entry_tf,
                "levels_timeframe": scoped_args.levels_tf,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(
        "pno-stage: summary=%s context=%s",
        summary_path,
        context_path,
    )
    return exit_code


def _run_ppa_stage_inner(config: AppConfig, args: argparse.Namespace) -> int:
    logger = get_logger("ppa-stage", level=config.backtest.log_level, logs_dir=config.backtest.logs_dir)
    preset_stage, preset_through_stage, preset_name = _resolve_ppa_stage_preset(getattr(args, "preset", None))
    timeframes = _resolve_ppa_research_timeframes(args)
    timestamp_label = time.strftime("%Y%m%d_%H%M%S")
    root_output_dir = (
        Path(args.output_dir)
        if getattr(args, "output_dir", None)
        else Path(config.backtest.results_dir) / "stage_review" / "post_pump_absorption" / preset_name / timestamp_label
    )
    root_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "ppa-stage: preset=%s stage=%s through_stage=%s timeframes=%s output_dir=%s",
        preset_name,
        preset_stage,
        preset_through_stage,
        ",".join(timeframe.value for timeframe in timeframes),
        root_output_dir,
    )

    run_exit_codes: list[int] = []
    summary_rows: list[dict[str, object]] = []
    if len(timeframes) > 1:
        logger.info("ppa-stage: parallel timeframes enabled workers=%s", len(timeframes))
        ordered_results: dict[str, tuple[int, list[dict[str, object]]]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(timeframes)) as executor:
            futures = {
                executor.submit(
                    _run_ppa_stage_timeframe_job,
                    config=config,
                    args=args,
                    timeframe=timeframe,
                    preset_stage=preset_stage,
                    preset_through_stage=preset_through_stage,
                    preset_name=preset_name,
                    root_output_dir=root_output_dir,
                ): timeframe.value
                for timeframe in timeframes
            }
            for future in concurrent.futures.as_completed(futures):
                timeframe_value, exit_code, timeframe_rows = future.result()
                ordered_results[timeframe_value] = (exit_code, timeframe_rows)
                logger.info(
                    "ppa-stage: timeframe=%s completed exit_code=%s summary_rows=%s",
                    timeframe_value,
                    exit_code,
                    len(timeframe_rows),
                )

        for timeframe in timeframes:
            exit_code, timeframe_rows = ordered_results[timeframe.value]
            run_exit_codes.append(exit_code)
            summary_rows.extend(timeframe_rows)
    else:
        timeframe_value, exit_code, timeframe_rows = _run_ppa_stage_timeframe_job(
            config=config,
            args=args,
            timeframe=timeframes[0],
            preset_stage=preset_stage,
            preset_through_stage=preset_through_stage,
            preset_name=preset_name,
            root_output_dir=root_output_dir,
        )
        logger.info(
            "ppa-stage: timeframe=%s completed exit_code=%s summary_rows=%s",
            timeframe_value,
            exit_code,
            len(timeframe_rows),
        )
        run_exit_codes.append(exit_code)
        summary_rows.extend(timeframe_rows)

    summary_path = root_output_dir / "stage_review_summary.csv"
    pd.DataFrame(summary_rows).to_csv(summary_path, index=False)
    context_path = root_output_dir / "stage_review_context.json"
    context_path.write_text(
        json.dumps(
            {
                "preset": preset_name,
                "stage": preset_stage,
                "through_stage": preset_through_stage,
                "timeframes": [timeframe.value for timeframe in timeframes],
                "symbols": list(getattr(args, "symbols", None) or []),
                "top_n": getattr(args, "top_n", None),
                "ppa_profile": getattr(args, "ppa_profile", None) or config.strategy.post_pump_absorption_profile,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(
        "ppa-stage: summary=%s context=%s",
        summary_path,
        context_path,
    )
    return max(run_exit_codes, default=0)


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


def fetch_ppa_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Загружает кэш для post_pump_absorption без OI: 1m/3m/5m только OHLCV."""
    return _run_with_logging("fetch-ppa-cache", config, lambda: _fetch_ppa_cache_inner(config, args))


def update_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Обновляет локальный кэш данных."""
    return _run_with_logging("update-cache", config, lambda: _update_cache_inner(config, args))


def run_backtest(config: AppConfig, args: argparse.Namespace) -> int:
    """Запускает бэктест по текущей конфигурации."""
    return _run_with_logging("run-backtest", config, lambda: _run_backtest_inner(config, args))


def run_ppa_research(config: AppConfig, args: argparse.Namespace) -> int:
    """Runs post_pump_absorption on multiple micro timeframes and builds research artifacts."""
    return _run_with_logging("run-ppa-research", config, lambda: _run_ppa_research_inner(config, args))


def run_hourly_pump_research(config: AppConfig, args: argparse.Namespace) -> int:
    """Runs hourly Asia-session pump research on cached micro timeframes."""
    return _run_with_logging("run-hourly-pump-research", config, lambda: _run_hourly_pump_research_inner(config, args))


def run_hourly_pump_static_combo_analysis(config: AppConfig, args: argparse.Namespace) -> int:
    """Builds static full-year hourly pump combo catalog and priority-selected portfolio."""
    return _run_with_logging(
        "run-hourly-pump-static-combo-analysis",
        config,
        lambda: _run_hourly_pump_static_combo_analysis_inner(config, args),
    )


def run_hourly_pump_production_report(config: AppConfig, args: argparse.Namespace) -> int:
    """Builds a fixed production report and KPI gate from a static combo run."""
    return _run_with_logging(
        "run-hourly-pump-production-report",
        config,
        lambda: _run_hourly_pump_production_report_inner(config, args),
    )


def run_hourly_pump_unified_analysis(config: AppConfig, args: argparse.Namespace) -> int:
    """Builds one unified XX:00 strategy without hour-specific branches."""
    return _run_with_logging(
        "run-hourly-pump-unified-analysis",
        config,
        lambda: _run_hourly_pump_unified_analysis_inner(config, args),
    )


def run_hourly_pump_unified_edge_search(config: AppConfig, args: argparse.Namespace) -> int:
    """Runs a unified full-year execution search without hour-specific branches."""
    return _run_with_logging(
        "run-hourly-pump-unified-edge-search",
        config,
        lambda: _run_hourly_pump_unified_edge_search_inner(config, args),
    )


def run_hourly_pump_short_edge_search(config: AppConfig, args: argparse.Namespace) -> int:
    """Runs a unified short-side search after XX:00 anomalies."""
    return _run_with_logging(
        "run-hourly-pump-short-edge-search",
        config,
        lambda: _run_hourly_pump_short_edge_search_inner(config, args),
    )


def run_hourly_pump_session_short_search(config: AppConfig, args: argparse.Namespace) -> int:
    """Runs session-based short-edge search from raw cache with minute-of-hour comparison."""
    return _run_with_logging(
        "run-hourly-pump-session-short-search",
        config,
        lambda: _run_hourly_pump_session_short_search_inner(config, args),
    )


def run_ppa_stage(config: AppConfig, args: argparse.Namespace) -> int:
    """Runs compact stage review for post_pump_absorption across micro timeframes."""
    return _run_with_logging("ppa-stage", config, lambda: _run_ppa_stage_inner(config, args))


def run_pno_stage(config: AppConfig, args: argparse.Namespace) -> int:
    """Runs compact stage review for PNO on the standard 1m/5m pipeline."""
    return _run_with_logging("pno-stage", config, lambda: _run_pno_stage_inner(config, args))


def check_quality(config: AppConfig, args: argparse.Namespace) -> int:
    """Проверяет качество и целостность данных."""
    return _run_with_logging("check-quality", config, lambda: _check_quality_inner(config, args))


def clear_cache(config: AppConfig, args: argparse.Namespace) -> int:
    """Очищает директорию локального кэша и пересоздаёт её."""
    return _run_with_logging("clear-cache", config, lambda: _clear_cache_inner(config, args))




