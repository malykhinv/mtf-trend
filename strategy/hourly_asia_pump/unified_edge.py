from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from domain.enums.timeframe import Timeframe
from strategy.hourly_asia_pump.research import (
    _event_matches_trade_model,
    _find_trade_model_entry,
    _format_duration,
    _lower_timeframe_for_execution,
    _progress_snapshot,
    _simulate_trade_model_from_entry,
)
from strategy.hourly_asia_pump.static_combo import (
    _build_monthly_returns_frame,
    _calendar_months_from_frames,
    _frame_to_markdown,
    _index_to_variant_label,
    _save_priority_equity_curve_chart,
    _save_priority_monthly_returns_chart,
    _save_priority_trade_distribution_chart,
    _save_priority_trade_timeline_chart,
    _sort_summary_frame,
    _summarize_events,
)
from strategy.hourly_asia_pump.static_combo_trade_plotter import (
    StaticComboTradePlotSpec,
    StaticComboTradePlotter,
)
from vectorbt_runner.data_preparer import DataPreparer

module_logger = logging.getLogger(__name__)

_EDGE_MIN_TRADES_PER_YEAR = 50.0
_EDGE_MIN_MEAN_RETURN_PCT = 0.025
_EDGE_MIN_WIN_RATE = 0.40
_EDGE_MIN_ANNUALIZED_UNIT_PNL_PCT = 1.0
_EDGE_MAX_DRAWDOWN_PCT = 0.30
_EDGE_MIN_POSITIVE_MONTHS = 9
_EDGE_MIN_STABLE_POSITIVE_MONTHS = 7
_EDGE_MAX_TOP3_TRADE_SHARE = 0.70
_EDGE_MAX_TOP5_TRADE_SHARE = 0.85
_EDGE_MAX_BEST_MONTH_SHARE = 0.55
_EDGE_MAX_TOP3_SYMBOL_SHARE = 0.70
_EDGE_MAX_TOP5_SYMBOL_SHARE = 0.85
_EDGE_COMBO_CANDIDATE_LIMIT = 24
_EDGE_MAX_COMBO_COMPONENTS = 3
_EDGE_PROGRESS_LOG_EVERY_SYMBOLS = 25
_EDGE_EQUITY_RISK_FRACTION = 0.04
_EDGE_EQUITY_MAX_NOTIONAL_FRACTION = 1.0
_EDGE_HOLDOUT_SPLITS: tuple[tuple[str, int], ...] = (
    ("train8_test4", 8),
    ("train9_test3", 9),
)


@dataclass(frozen=True, slots=True)
class UnifiedExecutionModel:
    model_id: str
    label: str
    signal_profile_id: str
    entry_profile_id: str
    exit_profile_id: str
    entry_style: str
    trail_style: str
    initial_stop_style: str
    min_trigger_return_pct: float
    min_range_atr: float
    min_body_atr: float
    min_volume_mult: float
    max_close_to_high_frac: float
    max_entry_bars: int
    max_pullback_frac: float
    pullback_volume_frac: float
    flag_bars: int
    flag_max_range_frac: float
    partial_take_pct: float
    partial_take_r: float
    partial_fraction: float
    move_stop_to_be_after_partial: bool
    trail_activation_pct: float
    fast_fail_bars: int
    fast_fail_min_return_pct: float
    max_hold_minutes: int
    max_pre_base_range_pct_60m: float | None = None
    max_pre_base_drift_pct_60m: float | None = None
    max_pre_base_range_vs_trigger: float | None = None
    max_pre_entry_pullback_frac: float | None = None
    max_pre_entry_red_volume_frac: float | None = None
    min_pre_entry_low_frac_of_trigger_range: float | None = None
    min_pre_base_range_pct_60m: float | None = None
    min_pre_base_drift_pct_60m: float | None = None
    min_pre_base_range_vs_trigger: float | None = None
    require_next_bar_green: bool = False
    max_next_bar_pullback_frac: float | None = None
    min_next_bar_low_frac_of_trigger_range: float | None = None
    min_next_bar_return_pct: float | None = None
    min_next_close_pos_in_bar: float | None = None
    min_next_extension_above_trigger_high_pct: float | None = None
    breakeven_activation_pct: float = 0.0


def _safe_float(value: object) -> float | None:
    if value is None or value is pd.NA:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _sanitize_filename(value: str) -> str:
    sanitized = "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in value)
    while "__" in sanitized:
        sanitized = sanitized.replace("__", "_")
    return sanitized.strip("_") or "item"


def _clear_png_files(directory: Path) -> None:
    if not directory.exists():
        return
    for path in directory.glob("*.png"):
        path.unlink(missing_ok=True)


def _prepare_selected_events(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if frame.empty:
        return frame
    base_columns = [
        "symbol",
        "timeframe",
        "row_index",
        "timestamp_ms",
        "timestamp_utc",
        "date_utc",
        "hour_utc",
        "trigger_open",
        "trigger_high",
        "trigger_low",
        "trigger_close",
        "trigger_volume",
        "trigger_return_pct",
        "trigger_range_pct",
        "range_atr",
        "body_atr",
        "volume_mult",
        "close_to_high_frac",
        "breakout_pct",
        "pre_base_range_pct_60m",
        "pre_base_drift_pct_60m",
        "pre_base_range_vs_trigger",
        "atr_window_minutes",
        "volume_window_minutes",
        "breakout_lookback_minutes",
        "max_follow_minutes",
        "peak_price_before_50pct_retrace",
        "peak_timestamp_ms",
        "peak_timestamp_utc",
        "peak_return_pct",
        "continuation_peak_return_pct",
        "bars_to_peak",
        "minutes_to_peak",
        "retraced_50pct_within_window",
        "retrace_50_timestamp_ms",
        "retrace_50_timestamp_utc",
        "bars_to_50pct_retrace",
        "minutes_to_50pct_retrace",
        "max_return_next_5m_pct",
        "close_return_5m_pct",
        "survived_5m",
        "max_return_next_15m_pct",
        "close_return_15m_pct",
        "survived_15m",
        "max_return_next_30m_pct",
        "close_return_30m_pct",
        "survived_30m",
        "max_return_next_60m_pct",
        "close_return_60m_pct",
        "survived_60m",
        "grid_id",
        "profile_id",
        "selection_min_range_atr",
        "selection_min_body_atr",
        "selection_min_volume_mult",
        "selection_max_close_to_high_frac",
        "selection_min_breakout_pct",
        "selection_profile",
    ]
    prepared = frame[[column for column in base_columns if column in frame.columns]].copy()
    prepared["timestamp_ms"] = pd.to_numeric(prepared["timestamp_ms"], errors="coerce")
    prepared["row_index"] = pd.to_numeric(prepared["row_index"], errors="coerce")
    prepared["timestamp_utc"] = pd.to_datetime(prepared["timestamp_utc"], utc=True, errors="coerce")
    prepared["month_utc"] = prepared["timestamp_utc"].dt.strftime("%Y-%m")
    prepared = prepared.sort_values(["symbol", "timestamp_ms"]).drop_duplicates(
        ["symbol", "timeframe", "row_index", "timestamp_ms"],
        keep="first",
    )
    return prepared.reset_index(drop=True)


def _build_signal_profiles() -> tuple[dict[str, Any], ...]:
    profiles: list[dict[str, Any]] = []
    base_profiles = (
        ("sig_50", 0.050, 5.0, 5.0, 0.18),
        ("sig_55", 0.055, 5.5, 6.0, 0.17),
        ("sig_60", 0.060, 6.5, 7.0, 0.16),
        ("sig_65", 0.065, 7.5, 9.0, 0.15),
        ("sig_70", 0.070, 8.5, 12.0, 0.14),
    )
    for signal_profile_id, min_trigger_return_pct, min_range_atr, min_volume_mult, max_close_to_high_frac in base_profiles:
        profiles.append(
            {
                "signal_profile_id": signal_profile_id,
                "min_trigger_return_pct": min_trigger_return_pct,
                "min_range_atr": min_range_atr,
                "min_body_atr": 0.0,
                "min_volume_mult": min_volume_mult,
                "max_close_to_high_frac": max_close_to_high_frac,
                "min_pre_base_range_pct_60m": None,
                "min_pre_base_drift_pct_60m": None,
                "min_pre_base_range_vs_trigger": None,
            }
        )

    profiles.extend(
        (
            {
                "signal_profile_id": "sig_60_wake",
                "min_trigger_return_pct": 0.060,
                "min_range_atr": 6.5,
                "min_body_atr": 0.0,
                "min_volume_mult": 7.0,
                "max_close_to_high_frac": 0.16,
                "min_pre_base_range_pct_60m": 0.02,
                "min_pre_base_drift_pct_60m": 0.010,
                "min_pre_base_range_vs_trigger": 0.15,
            },
            {
                "signal_profile_id": "sig_65_wake",
                "min_trigger_return_pct": 0.065,
                "min_range_atr": 7.5,
                "min_body_atr": 0.0,
                "min_volume_mult": 9.0,
                "max_close_to_high_frac": 0.15,
                "min_pre_base_range_pct_60m": 0.03,
                "min_pre_base_drift_pct_60m": 0.015,
                "min_pre_base_range_vs_trigger": 0.20,
            },
            {
                "signal_profile_id": "sig_65_strong_wake",
                "min_trigger_return_pct": 0.065,
                "min_range_atr": 7.5,
                "min_body_atr": 0.0,
                "min_volume_mult": 9.0,
                "max_close_to_high_frac": 0.15,
                "min_pre_base_range_pct_60m": 0.04,
                "min_pre_base_drift_pct_60m": 0.020,
                "min_pre_base_range_vs_trigger": 0.30,
            },
            {
                "signal_profile_id": "sig_70_wake",
                "min_trigger_return_pct": 0.070,
                "min_range_atr": 8.5,
                "min_body_atr": 0.0,
                "min_volume_mult": 12.0,
                "max_close_to_high_frac": 0.14,
                "min_pre_base_range_pct_60m": 0.04,
                "min_pre_base_drift_pct_60m": 0.025,
                "min_pre_base_range_vs_trigger": 0.30,
            },
        )
    )
    return tuple(profiles)


def _build_entry_profiles() -> tuple[dict[str, Any], ...]:
    return (
        {"entry_profile_id": "next_open", "entry_style": "next_bar_open", "max_entry_bars": 1, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_25g", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.25, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_25g_strong", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.25, "min_next_bar_low_frac_of_trigger_range": 0.50, "min_next_bar_return_pct": 0.0, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_25g_strong_pos50", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.25, "min_next_bar_low_frac_of_trigger_range": 0.50, "min_next_bar_return_pct": 0.0, "min_next_close_pos_in_bar": 0.50, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_25g_strong_ext5", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.25, "min_next_bar_low_frac_of_trigger_range": 0.50, "min_next_bar_return_pct": 0.0, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": 0.005, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_25g_strong_pos50_ext5", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.25, "min_next_bar_low_frac_of_trigger_range": 0.50, "min_next_bar_return_pct": 0.0, "min_next_close_pos_in_bar": 0.50, "min_next_extension_above_trigger_high_pct": 0.005, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_25g_strong_pos50_ext5_ret5", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.25, "min_next_bar_low_frac_of_trigger_range": 0.50, "min_next_bar_return_pct": 0.005, "min_next_close_pos_in_bar": 0.50, "min_next_extension_above_trigger_high_pct": 0.005, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_33", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": 0.33, "min_next_bar_low_frac_of_trigger_range": 0.35, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_50g", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.50, "min_next_bar_low_frac_of_trigger_range": 0.25, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_50g_ret5", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.50, "min_next_bar_low_frac_of_trigger_range": 0.25, "min_next_bar_return_pct": 0.005, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_50g_pos50", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.50, "min_next_bar_low_frac_of_trigger_range": 0.25, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": 0.50, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_50g_ext5", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.50, "min_next_bar_low_frac_of_trigger_range": 0.25, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": 0.005, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_50g_pos50_ext5", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.50, "min_next_bar_low_frac_of_trigger_range": 0.25, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": 0.50, "min_next_extension_above_trigger_high_pct": 0.005, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_50g_pos50_ext5_ret5", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.50, "min_next_bar_low_frac_of_trigger_range": 0.25, "min_next_bar_return_pct": 0.005, "min_next_close_pos_in_bar": 0.50, "min_next_extension_above_trigger_high_pct": 0.005, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "break_1", "entry_style": "break_trigger_high", "max_entry_bars": 1, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "break_2", "entry_style": "break_trigger_high", "max_entry_bars": 2, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "break_2_hold", "entry_style": "break_trigger_high", "max_entry_bars": 2, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": 0.25, "max_pre_entry_red_volume_frac": 0.80, "min_pre_entry_low_frac_of_trigger_range": 0.50},
        {"entry_profile_id": "pullback_25", "entry_style": "pullback_reclaim", "max_entry_bars": 3, "max_pullback_frac": 0.25, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": 0.35, "max_pre_entry_red_volume_frac": 1.0, "min_pre_entry_low_frac_of_trigger_range": 0.35},
        {"entry_profile_id": "pressure_25", "entry_style": "pressure_reclaim", "max_entry_bars": 3, "max_pullback_frac": 0.25, "pullback_volume_frac": 0.80, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": 0.35, "max_pre_entry_red_volume_frac": 0.80, "min_pre_entry_low_frac_of_trigger_range": 0.35},
        {"entry_profile_id": "flag_2", "entry_style": "flag_break", "max_entry_bars": 2, "max_pullback_frac": 0.35, "pullback_volume_frac": 1.0, "flag_bars": 2, "flag_max_range_frac": 0.60, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "min_next_close_pos_in_bar": None, "min_next_extension_above_trigger_high_pct": None, "max_pre_entry_pullback_frac": 0.35, "max_pre_entry_red_volume_frac": 1.0, "min_pre_entry_low_frac_of_trigger_range": 0.35},
    )


def _build_exit_profiles() -> tuple[dict[str, Any], ...]:
    return (
        {"exit_profile_id": "runner_wide", "initial_stop_style": "trigger_low", "trail_style": "last_red_low", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.05, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "runner_wide_be2", "initial_stop_style": "trigger_low", "trail_style": "last_red_low", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.05, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 720, "breakeven_activation_pct": 0.02},
        {"exit_profile_id": "runner_wide_be3", "initial_stop_style": "trigger_low", "trail_style": "last_red_low", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.05, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 720, "breakeven_activation_pct": 0.03},
        {"exit_profile_id": "runner_last_low", "initial_stop_style": "pattern_low", "trail_style": "prev_bar_low", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.0, "fast_fail_bars": 2, "fast_fail_min_return_pct": 0.005, "max_hold_minutes": 360, "breakeven_activation_pct": 0.02},
        {"exit_profile_id": "runner_mid_stop_be2", "initial_stop_style": "trigger_body_mid", "trail_style": "last_red_low", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.03, "fast_fail_bars": 1, "fast_fail_min_return_pct": 0.005, "max_hold_minutes": 360, "breakeven_activation_pct": 0.02},
        {"exit_profile_id": "runner_mid_stop_be3", "initial_stop_style": "trigger_body_mid", "trail_style": "last_red_low", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.03, "fast_fail_bars": 1, "fast_fail_min_return_pct": 0.005, "max_hold_minutes": 360, "breakeven_activation_pct": 0.03},
        {"exit_profile_id": "runner_quarter_stop_be2", "initial_stop_style": "trigger_body_upper_quarter", "trail_style": "last_red_low", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.02, "fast_fail_bars": 1, "fast_fail_min_return_pct": 0.005, "max_hold_minutes": 240, "breakeven_activation_pct": 0.02},
        {"exit_profile_id": "partial4_be", "initial_stop_style": "trigger_low", "trail_style": "last_red_low", "partial_take_pct": 0.04, "partial_take_r": 1.5, "partial_fraction": 0.40, "move_stop_to_be_after_partial": True, "trail_activation_pct": 0.04, "fast_fail_bars": 2, "fast_fail_min_return_pct": 0.005, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "partial5_be", "initial_stop_style": "trigger_low", "trail_style": "last_red_low", "partial_take_pct": 0.05, "partial_take_r": 2.0, "partial_fraction": 0.50, "move_stop_to_be_after_partial": True, "trail_activation_pct": 0.05, "fast_fail_bars": 2, "fast_fail_min_return_pct": 0.01, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "partial6_be", "initial_stop_style": "trigger_low", "trail_style": "last_red_low", "partial_take_pct": 0.06, "partial_take_r": 2.5, "partial_fraction": 0.50, "move_stop_to_be_after_partial": True, "trail_activation_pct": 0.05, "fast_fail_bars": 2, "fast_fail_min_return_pct": 0.01, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "time_120", "initial_stop_style": "trigger_low", "trail_style": "none", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.0, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 120, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "time_180", "initial_stop_style": "trigger_low", "trail_style": "none", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.0, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 180, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "time_240", "initial_stop_style": "trigger_low", "trail_style": "none", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.0, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 240, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "tp4_full", "initial_stop_style": "trigger_low", "trail_style": "none", "partial_take_pct": 0.04, "partial_take_r": 0.0, "partial_fraction": 1.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.0, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "tp5_full", "initial_stop_style": "trigger_low", "trail_style": "none", "partial_take_pct": 0.05, "partial_take_r": 0.0, "partial_fraction": 1.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.0, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "tp6_full", "initial_stop_style": "trigger_low", "trail_style": "none", "partial_take_pct": 0.06, "partial_take_r": 0.0, "partial_fraction": 1.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.0, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "tp5_confirm_low", "initial_stop_style": "confirmed_bar_low", "trail_style": "none", "partial_take_pct": 0.05, "partial_take_r": 0.0, "partial_fraction": 1.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.0, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "tp6_confirm_low", "initial_stop_style": "confirmed_bar_low", "trail_style": "none", "partial_take_pct": 0.06, "partial_take_r": 0.0, "partial_fraction": 1.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.0, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "partial5_confirm_low_be", "initial_stop_style": "confirmed_bar_low", "trail_style": "last_red_low", "partial_take_pct": 0.05, "partial_take_r": 0.0, "partial_fraction": 0.50, "move_stop_to_be_after_partial": True, "trail_activation_pct": 0.03, "fast_fail_bars": 1, "fast_fail_min_return_pct": 0.005, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "partial6_confirm_low_be", "initial_stop_style": "confirmed_bar_low", "trail_style": "last_red_low", "partial_take_pct": 0.06, "partial_take_r": 0.0, "partial_fraction": 0.50, "move_stop_to_be_after_partial": True, "trail_activation_pct": 0.03, "fast_fail_bars": 1, "fast_fail_min_return_pct": 0.005, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "runner_confirm_low_be3", "initial_stop_style": "confirmed_bar_low", "trail_style": "last_red_low", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.03, "fast_fail_bars": 1, "fast_fail_min_return_pct": 0.005, "max_hold_minutes": 360, "breakeven_activation_pct": 0.03},
        {"exit_profile_id": "tp5_confirm_body", "initial_stop_style": "confirmed_bar_body_low", "trail_style": "none", "partial_take_pct": 0.05, "partial_take_r": 0.0, "partial_fraction": 1.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.0, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "tp6_confirm_body", "initial_stop_style": "confirmed_bar_body_low", "trail_style": "none", "partial_take_pct": 0.06, "partial_take_r": 0.0, "partial_fraction": 1.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.0, "fast_fail_bars": 0, "fast_fail_min_return_pct": 0.0, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "partial5_confirm_body_be", "initial_stop_style": "confirmed_bar_body_low", "trail_style": "last_red_low", "partial_take_pct": 0.05, "partial_take_r": 0.0, "partial_fraction": 0.50, "move_stop_to_be_after_partial": True, "trail_activation_pct": 0.03, "fast_fail_bars": 1, "fast_fail_min_return_pct": 0.005, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "partial6_confirm_body_be", "initial_stop_style": "confirmed_bar_body_low", "trail_style": "last_red_low", "partial_take_pct": 0.06, "partial_take_r": 0.0, "partial_fraction": 0.50, "move_stop_to_be_after_partial": True, "trail_activation_pct": 0.03, "fast_fail_bars": 1, "fast_fail_min_return_pct": 0.005, "max_hold_minutes": 720, "breakeven_activation_pct": 0.0},
        {"exit_profile_id": "runner_confirm_body_be3", "initial_stop_style": "confirmed_bar_body_low", "trail_style": "last_red_low", "partial_take_pct": 0.0, "partial_take_r": 0.0, "partial_fraction": 0.0, "move_stop_to_be_after_partial": False, "trail_activation_pct": 0.03, "fast_fail_bars": 1, "fast_fail_min_return_pct": 0.005, "max_hold_minutes": 360, "breakeven_activation_pct": 0.03},
    )


def _build_rule_text(model: UnifiedExecutionModel) -> str:
    parts = [
        model.signal_profile_id,
        model.entry_profile_id,
        model.exit_profile_id,
        f"trigger>={model.min_trigger_return_pct:.3f}",
        f"range_atr>={model.min_range_atr:.1f}",
        f"volume>={model.min_volume_mult:.1f}",
        f"close_to_high<={model.max_close_to_high_frac:.2f}",
    ]
    if model.min_pre_base_range_pct_60m is not None:
        parts.append(f"pre_range>={model.min_pre_base_range_pct_60m:.3f}")
    if model.min_pre_base_drift_pct_60m is not None:
        parts.append(f"pre_drift>={model.min_pre_base_drift_pct_60m:.3f}")
    if model.min_pre_base_range_vs_trigger is not None:
        parts.append(f"pre_vs_trigger>={model.min_pre_base_range_vs_trigger:.2f}")
    if model.max_next_bar_pullback_frac is not None:
        parts.append(f"next_pb<={model.max_next_bar_pullback_frac:.2f}")
    if model.min_next_bar_low_frac_of_trigger_range is not None:
        parts.append(f"next_low>={model.min_next_bar_low_frac_of_trigger_range:.2f}")
    if model.min_next_bar_return_pct is not None:
        parts.append(f"next_ret>={model.min_next_bar_return_pct:.3f}")
    if model.min_next_close_pos_in_bar is not None:
        parts.append(f"next_close_pos>={model.min_next_close_pos_in_bar:.2f}")
    if model.min_next_extension_above_trigger_high_pct is not None:
        parts.append(f"next_ext>={model.min_next_extension_above_trigger_high_pct:.3f}")
    if model.max_pre_entry_pullback_frac is not None:
        parts.append(f"entry_pb<={model.max_pre_entry_pullback_frac:.2f}")
    if model.max_pre_entry_red_volume_frac is not None:
        parts.append(f"entry_red_vol<={model.max_pre_entry_red_volume_frac:.2f}")
    if model.min_pre_entry_low_frac_of_trigger_range is not None:
        parts.append(f"entry_low>={model.min_pre_entry_low_frac_of_trigger_range:.2f}")
    parts.append(f"stop={model.initial_stop_style}")
    parts.append(f"trail={model.trail_style}")
    if model.partial_fraction > 0.0:
        parts.append(f"partial={model.partial_fraction:.2f}@{model.partial_take_pct:.3f}")
    if model.breakeven_activation_pct > 0.0:
        parts.append(f"be@{model.breakeven_activation_pct:.3f}")
    parts.append(f"hold<={model.max_hold_minutes}m")
    return "; ".join(parts)


def _build_human_description(model: UnifiedExecutionModel) -> str:
    return f"Сигнал `{model.signal_profile_id}`. Вход `{model.entry_profile_id}`. Сопровождение `{model.exit_profile_id}`."


def _build_execution_models() -> list[UnifiedExecutionModel]:
    models: list[UnifiedExecutionModel] = []
    confirmed_only_stop_styles = {"confirmed_bar_low", "confirmed_bar_body_low"}
    for signal_profile in _build_signal_profiles():
        for entry_profile in _build_entry_profiles():
            for exit_profile in _build_exit_profiles():
                if entry_profile["entry_style"] == "next_bar_open" and exit_profile["initial_stop_style"] == "pattern_low":
                    continue
                if (
                    exit_profile["initial_stop_style"] in confirmed_only_stop_styles
                    and entry_profile["entry_style"] != "confirmed_next_open"
                ):
                    continue
                model_hash = hashlib.md5(
                    json.dumps({"signal": signal_profile, "entry": entry_profile, "exit": exit_profile}, sort_keys=True).encode("utf-8")
                ).hexdigest()[:12]
                models.append(
                    UnifiedExecutionModel(
                        model_id=f"ue_{model_hash}",
                        label=f"{signal_profile['signal_profile_id']} | {entry_profile['entry_profile_id']} | {exit_profile['exit_profile_id']}",
                        signal_profile_id=str(signal_profile["signal_profile_id"]),
                        entry_profile_id=str(entry_profile["entry_profile_id"]),
                        exit_profile_id=str(exit_profile["exit_profile_id"]),
                        entry_style=str(entry_profile["entry_style"]),
                        trail_style=str(exit_profile["trail_style"]),
                        initial_stop_style=str(exit_profile["initial_stop_style"]),
                        min_trigger_return_pct=float(signal_profile["min_trigger_return_pct"]),
                        min_range_atr=float(signal_profile["min_range_atr"]),
                        min_body_atr=float(signal_profile["min_body_atr"]),
                        min_volume_mult=float(signal_profile["min_volume_mult"]),
                        max_close_to_high_frac=float(signal_profile["max_close_to_high_frac"]),
                        max_entry_bars=int(entry_profile["max_entry_bars"]),
                        max_pullback_frac=float(entry_profile["max_pullback_frac"]),
                        pullback_volume_frac=float(entry_profile["pullback_volume_frac"]),
                        flag_bars=int(entry_profile["flag_bars"]),
                        flag_max_range_frac=float(entry_profile["flag_max_range_frac"]),
                        partial_take_pct=float(exit_profile["partial_take_pct"]),
                        partial_take_r=float(exit_profile["partial_take_r"]),
                        partial_fraction=float(exit_profile["partial_fraction"]),
                        move_stop_to_be_after_partial=bool(exit_profile["move_stop_to_be_after_partial"]),
                        trail_activation_pct=float(exit_profile["trail_activation_pct"]),
                        fast_fail_bars=int(exit_profile["fast_fail_bars"]),
                        fast_fail_min_return_pct=float(exit_profile["fast_fail_min_return_pct"]),
                        max_hold_minutes=int(exit_profile["max_hold_minutes"]),
                        min_pre_base_range_pct_60m=_safe_float(signal_profile.get("min_pre_base_range_pct_60m")),
                        min_pre_base_drift_pct_60m=_safe_float(signal_profile.get("min_pre_base_drift_pct_60m")),
                        min_pre_base_range_vs_trigger=_safe_float(signal_profile.get("min_pre_base_range_vs_trigger")),
                        require_next_bar_green=bool(entry_profile["require_next_bar_green"]),
                        max_next_bar_pullback_frac=_safe_float(entry_profile.get("max_next_bar_pullback_frac")),
                        min_next_bar_low_frac_of_trigger_range=_safe_float(entry_profile.get("min_next_bar_low_frac_of_trigger_range")),
                        min_next_bar_return_pct=_safe_float(entry_profile.get("min_next_bar_return_pct")),
                        min_next_close_pos_in_bar=_safe_float(entry_profile.get("min_next_close_pos_in_bar")),
                        min_next_extension_above_trigger_high_pct=_safe_float(entry_profile.get("min_next_extension_above_trigger_high_pct")),
                        max_pre_entry_pullback_frac=_safe_float(entry_profile.get("max_pre_entry_pullback_frac")),
                        max_pre_entry_red_volume_frac=_safe_float(entry_profile.get("max_pre_entry_red_volume_frac")),
                        min_pre_entry_low_frac_of_trigger_range=_safe_float(entry_profile.get("min_pre_entry_low_frac_of_trigger_range")),
                        breakeven_activation_pct=float(exit_profile["breakeven_activation_pct"]),
                    )
                )
    return models


def _simulate_unified_execution_events(
    *,
    preparer: DataPreparer,
    timeframe: Timeframe,
    selected_events: pd.DataFrame,
    models: Sequence[UnifiedExecutionModel],
    commission_rate: float,
    logger: logging.Logger,
) -> pd.DataFrame:
    if selected_events.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    grouped = list(selected_events.groupby("symbol", sort=True))
    total_symbols = len(grouped)
    started_at = time.time()
    total_checked = 0
    total_triggered = 0

    for symbol_index, (symbol, group) in enumerate(grouped, start=1):
        frame = preparer.load_symbol_data(symbol, timeframe)
        if frame.empty:
            continue
        lower_timeframe = _lower_timeframe_for_execution(timeframe)
        micro_open_values: Any | None = None
        micro_high_values: Any | None = None
        micro_low_values: Any | None = None
        micro_timestamp_values: Any | None = None
        if lower_timeframe is not None:
            microframe = preparer.load_symbol_data(symbol, lower_timeframe)
            if not microframe.empty:
                micro_open_values = pd.to_numeric(microframe["open"], errors="coerce").to_numpy(dtype="float64")
                micro_high_values = pd.to_numeric(microframe["high"], errors="coerce").to_numpy(dtype="float64")
                micro_low_values = pd.to_numeric(microframe["low"], errors="coerce").to_numpy(dtype="float64")
                micro_timestamp_values = pd.to_numeric(microframe["timestamp"], errors="coerce").fillna(0).astype("int64").to_numpy()

        open_values = pd.to_numeric(frame["open"], errors="coerce").to_numpy(dtype="float64")
        high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype="float64")
        low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype="float64")
        close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype="float64")
        volume_values = pd.to_numeric(frame["volume"], errors="coerce").to_numpy(dtype="float64")
        timestamp_values = pd.to_numeric(frame["timestamp"], errors="coerce").fillna(0).astype("int64").to_numpy()

        for event in group.to_dict("records"):
            row_index = int(event["row_index"])
            for model in models:
                total_checked += 1
                if not _event_matches_trade_model(event, model):
                    continue
                entry = _find_trade_model_entry(
                    model=model,
                    event=event,
                    open_values=open_values,
                    high_values=high_values,
                    low_values=low_values,
                    close_values=close_values,
                    volume_values=volume_values,
                    timestamp_values=timestamp_values,
                    row_index=row_index,
                )
                if not bool(entry.get("trade_triggered")):
                    continue
                trade = _simulate_trade_model_from_entry(
                    model=model,
                    timeframe=timeframe,
                    open_values=open_values,
                    high_values=high_values,
                    low_values=low_values,
                    close_values=close_values,
                    timestamp_values=timestamp_values,
                    row_index=row_index,
                    entry_idx=int(entry["entry_idx"]),
                    entry_price=float(entry["entry_price"]),
                    initial_stop_price=float(entry["initial_stop_price"]),
                    entry_execution_mode=str(entry.get("entry_execution_mode") or "touch"),
                    commission_rate=commission_rate,
                    micro_open_values=micro_open_values,
                    micro_high_values=micro_high_values,
                    micro_low_values=micro_low_values,
                    micro_timestamp_values=micro_timestamp_values,
                )
                total_triggered += 1
                rows.append(
                    {
                        **event,
                        "trade_model_id": model.model_id,
                        "trade_model_label": model.label,
                        "signal_profile_id": model.signal_profile_id,
                        "entry_profile_id": model.entry_profile_id,
                        "exit_profile_id": model.exit_profile_id,
                        "initial_stop_style": model.initial_stop_style,
                        "trail_style": model.trail_style,
                        "partial_fraction": model.partial_fraction,
                        "partial_take_pct": model.partial_take_pct,
                        "breakeven_activation_pct": model.breakeven_activation_pct,
                        "max_hold_minutes": model.max_hold_minutes,
                        "rule_text": _build_rule_text(model),
                        "human_description": _build_human_description(model),
                        **entry,
                        **trade,
                    }
                )

        if symbol_index == 1 or symbol_index == total_symbols or symbol_index % _EDGE_PROGRESS_LOG_EVERY_SYMBOLS == 0:
            progress_pct, elapsed, eta_seconds = _progress_snapshot(
                completed=symbol_index,
                total=total_symbols,
                started_at=started_at,
            )
            logger.info(
                "unified-edge: stage=simulate progress=%.1f%% symbols=%s/%s checked=%s triggered=%s rows=%s elapsed=%s eta=%s",
                progress_pct,
                symbol_index,
                total_symbols,
                total_checked,
                total_triggered,
                len(rows),
                _format_duration(elapsed),
                _format_duration(eta_seconds),
            )

    return pd.DataFrame(rows)


def _top_share(returns: pd.Series, total_unit_pnl: float, top_n: int) -> float | None:
    if total_unit_pnl <= 0.0:
        return None
    winners = returns[returns > 0].sort_values(ascending=False)
    if winners.empty:
        return None
    return float(winners.head(top_n).sum() / total_unit_pnl)


def _top_group_share(grouped_returns: pd.Series, total_unit_pnl: float, top_n: int) -> float | None:
    if total_unit_pnl <= 0.0:
        return None
    winners = pd.to_numeric(grouped_returns, errors="coerce").dropna()
    winners = winners[winners > 0].sort_values(ascending=False)
    if winners.empty:
        return None
    return float(winners.head(top_n).sum() / total_unit_pnl)


def _build_symbol_concentration_metrics(
    frame: pd.DataFrame,
    *,
    calendar_months: Sequence[str],
) -> dict[str, object]:
    if frame.empty:
        return {
            "top1_symbol_pnl_share": None,
            "top3_symbol_pnl_share": None,
            "top5_symbol_pnl_share": None,
            "annualized_remove_top1_symbol_pct": 0.0,
            "annualized_remove_top3_symbol_pct": 0.0,
            "annualized_remove_top5_symbol_pct": 0.0,
        }
    ordered = frame.sort_values("entry_timestamp_ms").copy()
    returns = pd.to_numeric(ordered["exit_return_pct"], errors="coerce").fillna(0.0)
    total_unit_pnl = float(returns.sum())
    symbol_returns = (
        ordered.assign(exit_return_pct=returns)
        .groupby("symbol", sort=False)["exit_return_pct"]
        .sum()
        .sort_values(ascending=False)
    )

    def _summary_without_symbols(symbols: Sequence[str]) -> float:
        scoped = ordered[~ordered["symbol"].astype(str).isin([str(symbol) for symbol in symbols])].copy()
        summary = _summarize_events(scoped, calendar_months=calendar_months)
        if summary is None:
            return 0.0
        return float(summary.get("annualized_unit_pnl_pct", 0.0) or 0.0)

    top_symbols = symbol_returns.index.astype(str).tolist()
    return {
        "top1_symbol_pnl_share": _top_group_share(symbol_returns, total_unit_pnl, 1),
        "top3_symbol_pnl_share": _top_group_share(symbol_returns, total_unit_pnl, 3),
        "top5_symbol_pnl_share": _top_group_share(symbol_returns, total_unit_pnl, 5),
        "annualized_remove_top1_symbol_pct": _summary_without_symbols(top_symbols[:1]),
        "annualized_remove_top3_symbol_pct": _summary_without_symbols(top_symbols[:3]),
        "annualized_remove_top5_symbol_pct": _summary_without_symbols(top_symbols[:5]),
    }


def _build_month_stability_metrics(
    frame: pd.DataFrame,
    *,
    calendar_months: Sequence[str],
) -> dict[str, object]:
    if frame.empty:
        return {
            "stable_positive_months_count": 0,
            "fragile_positive_months_count": 0,
            "median_positive_month_top1_trade_share": None,
            "max_positive_month_top1_trade_share": None,
            "median_positive_month_top2_trade_share": None,
            "max_positive_month_top2_trade_share": None,
        }

    top1_shares: list[float] = []
    top2_shares: list[float] = []
    stable_positive_months = 0
    fragile_positive_months = 0

    for month in calendar_months:
        scoped = frame[frame["month_utc"].astype(str) == str(month)].copy()
        if scoped.empty:
            continue
        returns = pd.to_numeric(scoped["exit_return_pct"], errors="coerce").fillna(0.0)
        month_pnl = float(returns.sum())
        trades_count = int(len(returns))
        wins_count = int((returns > 0).sum())
        win_rate = float(wins_count / trades_count) if trades_count > 0 else 0.0
        if month_pnl <= 0.0:
            continue

        positive_returns = returns[returns > 0].sort_values(ascending=False)
        if not positive_returns.empty:
            top1_shares.append(float(positive_returns.head(1).sum() / month_pnl))
            top2_shares.append(float(positive_returns.head(2).sum() / month_pnl))

        if wins_count >= 2 and win_rate >= 0.50:
            stable_positive_months += 1
        else:
            fragile_positive_months += 1

    return {
        "stable_positive_months_count": int(stable_positive_months),
        "fragile_positive_months_count": int(fragile_positive_months),
        "median_positive_month_top1_trade_share": float(pd.Series(top1_shares, dtype="float64").median()) if top1_shares else None,
        "max_positive_month_top1_trade_share": float(max(top1_shares)) if top1_shares else None,
        "median_positive_month_top2_trade_share": float(pd.Series(top2_shares, dtype="float64").median()) if top2_shares else None,
        "max_positive_month_top2_trade_share": float(max(top2_shares)) if top2_shares else None,
    }


def _simulate_equity_risk_metrics(
    frame: pd.DataFrame,
    *,
    calendar_months: Sequence[str],
    risk_fraction: float = _EDGE_EQUITY_RISK_FRACTION,
    max_total_notional_fraction: float = _EDGE_EQUITY_MAX_NOTIONAL_FRACTION,
) -> tuple[dict[str, object], pd.DataFrame]:
    if frame.empty:
        monthly = pd.DataFrame(
            {
                "month_utc": list(calendar_months),
                "equity_month_pnl_pct": [0.0 for _ in calendar_months],
                "equity_trades_count": [0 for _ in calendar_months],
                "equity_win_rate": [0.0 for _ in calendar_months],
                "equity_mean_trade_pct": [0.0 for _ in calendar_months],
            }
        )
        return {
            "equity_risk_fraction": float(risk_fraction),
            "equity_total_return_pct": 0.0,
            "equity_annualized_return_pct": 0.0,
            "equity_final_equity": 1.0,
            "equity_max_drawdown_pct": 0.0,
            "equity_mean_trade_pct": 0.0,
            "equity_median_trade_pct": 0.0,
            "equity_mean_pos_trade_pct": 0.0,
            "equity_mean_neg_trade_pct": 0.0,
            "equity_positive_months_count": 0,
            "equity_non_positive_months_count": len(calendar_months),
            "equity_stable_positive_months_count": 0,
            "equity_fragile_positive_months_count": 0,
            "equity_max_notional_fraction": 0.0,
            "equity_max_open_positions": 0,
        }, monthly

    ordered = frame.copy()
    ordered["entry_timestamp_ms"] = pd.to_numeric(ordered.get("entry_timestamp_ms"), errors="coerce")
    ordered["exit_timestamp_ms"] = pd.to_numeric(ordered.get("exit_timestamp_ms"), errors="coerce")
    ordered["initial_risk_pct"] = pd.to_numeric(ordered.get("initial_risk_pct"), errors="coerce")
    ordered["exit_return_pct"] = pd.to_numeric(ordered.get("exit_return_pct"), errors="coerce")
    ordered = ordered.dropna(subset=["entry_timestamp_ms", "exit_timestamp_ms", "initial_risk_pct", "exit_return_pct"]).copy()
    ordered = ordered[ordered["initial_risk_pct"] > 0.0].copy()
    if ordered.empty:
        return _simulate_equity_risk_metrics(pd.DataFrame(), calendar_months=calendar_months, risk_fraction=risk_fraction, max_total_notional_fraction=max_total_notional_fraction)

    ordered["entry_timestamp_ms"] = ordered["entry_timestamp_ms"].astype("int64")
    ordered["exit_timestamp_ms"] = ordered["exit_timestamp_ms"].astype("int64")
    ordered = ordered.sort_values(["entry_timestamp_ms", "exit_timestamp_ms", "symbol"], ascending=[True, True, True]).reset_index(drop=True)

    equity = 1.0
    open_positions: list[dict[str, object]] = []
    account_trade_returns: list[float] = []
    monthly_rows: dict[str, dict[str, float]] = {
        str(month): {"equity_month_pnl_abs": 0.0, "equity_trades_count": 0.0, "equity_wins_count": 0.0}
        for month in calendar_months
    }
    equity_curve = [equity]
    max_notional_fraction = 0.0
    max_open_positions = 0

    entries_by_ts = {
        int(timestamp): group.copy()
        for timestamp, group in ordered.groupby("entry_timestamp_ms", sort=True)
    }
    unique_times = sorted(set(ordered["entry_timestamp_ms"].astype("int64").tolist()) | set(ordered["exit_timestamp_ms"].astype("int64").tolist()))

    for timestamp in unique_times:
        remaining_positions: list[dict[str, object]] = []
        for position in open_positions:
            trade = position["trade"]
            if int(trade["exit_timestamp_ms"]) == int(timestamp):
                account_return_pct = float(position["notional_fraction"]) * float(trade["exit_return_pct"])
                pnl_abs = float(position["equity_at_entry"]) * account_return_pct
                equity += pnl_abs
                month = str(trade.get("month_utc", ""))
                if month not in monthly_rows:
                    monthly_rows[month] = {"equity_month_pnl_abs": 0.0, "equity_trades_count": 0.0, "equity_wins_count": 0.0}
                monthly_rows[month]["equity_month_pnl_abs"] += pnl_abs
                monthly_rows[month]["equity_trades_count"] += 1.0
                if account_return_pct > 0.0:
                    monthly_rows[month]["equity_wins_count"] += 1.0
                account_trade_returns.append(account_return_pct)
                equity_curve.append(equity)
            else:
                remaining_positions.append(position)
        open_positions = remaining_positions

        entry_group = entries_by_ts.get(int(timestamp))
        if entry_group is None or entry_group.empty:
            continue

        for _, trade in entry_group.iterrows():
            used_notional_fraction = float(sum(float(position["notional_fraction"]) for position in open_positions))
            raw_notional_fraction = float(risk_fraction / float(trade["initial_risk_pct"]))
            free_notional_fraction = max(0.0, float(max_total_notional_fraction) - used_notional_fraction)
            notional_fraction = min(raw_notional_fraction, free_notional_fraction)
            if notional_fraction <= 0.0:
                continue

            if int(trade["exit_timestamp_ms"]) == int(timestamp):
                account_return_pct = notional_fraction * float(trade["exit_return_pct"])
                pnl_abs = equity * account_return_pct
                month = str(trade.get("month_utc", ""))
                if month not in monthly_rows:
                    monthly_rows[month] = {"equity_month_pnl_abs": 0.0, "equity_trades_count": 0.0, "equity_wins_count": 0.0}
                monthly_rows[month]["equity_month_pnl_abs"] += pnl_abs
                monthly_rows[month]["equity_trades_count"] += 1.0
                if account_return_pct > 0.0:
                    monthly_rows[month]["equity_wins_count"] += 1.0
                account_trade_returns.append(account_return_pct)
                equity += pnl_abs
                equity_curve.append(equity)
                continue

            open_positions.append(
                {
                    "trade": trade,
                    "equity_at_entry": equity,
                    "notional_fraction": float(notional_fraction),
                }
            )
            current_notional_fraction = float(sum(float(position["notional_fraction"]) for position in open_positions))
            max_notional_fraction = max(max_notional_fraction, current_notional_fraction)
            max_open_positions = max(max_open_positions, len(open_positions))

    peak_equity = 1.0
    max_drawdown_pct = 0.0
    for curve_equity in equity_curve:
        peak_equity = max(peak_equity, curve_equity)
        if peak_equity > 0.0:
            max_drawdown_pct = max(max_drawdown_pct, (peak_equity - curve_equity) / peak_equity)

    account_returns_series = pd.Series(account_trade_returns, dtype="float64")
    positive_returns = account_returns_series[account_returns_series > 0.0]
    negative_returns = account_returns_series[account_returns_series <= 0.0]

    monthly_rows_list: list[dict[str, object]] = []
    running_month_equity = 1.0
    for month in calendar_months:
        month_key = str(month)
        month_pnl_abs = float(monthly_rows.get(month_key, {}).get("equity_month_pnl_abs", 0.0))
        month_trades_count = int(monthly_rows.get(month_key, {}).get("equity_trades_count", 0.0))
        month_wins_count = int(monthly_rows.get(month_key, {}).get("equity_wins_count", 0.0))
        month_return_pct = month_pnl_abs / running_month_equity if running_month_equity > 0.0 else 0.0
        equity_win_rate = (month_wins_count / month_trades_count) if month_trades_count > 0 else 0.0
        equity_stable_positive_month = bool(month_return_pct > 0.0 and month_trades_count >= 2 and equity_win_rate >= 0.50)
        monthly_rows_list.append(
            {
                "month_utc": month_key,
                "equity_month_pnl_pct": float(month_return_pct),
                "equity_trades_count": month_trades_count,
                "equity_win_rate": equity_win_rate,
                "equity_mean_trade_pct": (month_return_pct / month_trades_count) if month_trades_count > 0 else 0.0,
                "equity_stable_positive_month": equity_stable_positive_month,
            }
        )
        running_month_equity += month_pnl_abs
    monthly_frame = pd.DataFrame(monthly_rows_list)
    equity_positive_months_count = int((monthly_frame["equity_month_pnl_pct"] > 0.0).sum())
    equity_stable_positive_months_count = int(monthly_frame["equity_stable_positive_month"].fillna(False).astype(bool).sum())

    months_count = max(1, len(calendar_months))
    annualized_return_pct = float(pow(max(equity, 0.0000001), 12.0 / months_count) - 1.0)
    metrics = {
        "equity_risk_fraction": float(risk_fraction),
        "equity_total_return_pct": float(equity - 1.0),
        "equity_annualized_return_pct": annualized_return_pct,
        "equity_final_equity": float(equity),
        "equity_max_drawdown_pct": float(max_drawdown_pct),
        "equity_mean_trade_pct": float(account_returns_series.mean()) if not account_returns_series.empty else 0.0,
        "equity_median_trade_pct": float(account_returns_series.median()) if not account_returns_series.empty else 0.0,
        "equity_mean_pos_trade_pct": float(positive_returns.mean()) if not positive_returns.empty else 0.0,
        "equity_mean_neg_trade_pct": float(negative_returns.mean()) if not negative_returns.empty else 0.0,
        "equity_positive_months_count": equity_positive_months_count,
        "equity_non_positive_months_count": int((monthly_frame["equity_month_pnl_pct"] <= 0.0).sum()),
        "equity_stable_positive_months_count": equity_stable_positive_months_count,
        "equity_fragile_positive_months_count": int(max(equity_positive_months_count - equity_stable_positive_months_count, 0)),
        "equity_max_notional_fraction": float(max_notional_fraction),
        "equity_max_open_positions": int(max_open_positions),
    }
    return metrics, monthly_frame


def _build_concentration_metrics(
    frame: pd.DataFrame,
    *,
    calendar_months: Sequence[str],
) -> dict[str, object]:
    if frame.empty:
        return {
            "top1_trade_pnl_share": None,
            "top3_trade_pnl_share": None,
            "top5_trade_pnl_share": None,
            "best_month_pnl_share": None,
            "annualized_remove_top1_trade_pct": 0.0,
            "annualized_remove_top3_trade_pct": 0.0,
            "annualized_remove_top5_trade_pct": 0.0,
            "annualized_remove_best_month_pct": 0.0,
        }
    ordered = frame.sort_values("entry_timestamp_ms").copy()
    returns = pd.to_numeric(ordered["exit_return_pct"], errors="coerce").fillna(0.0)
    total_unit_pnl = float(returns.sum())
    monthly_returns = ordered.groupby("month_utc", sort=True)["exit_return_pct"].sum().reindex(list(calendar_months), fill_value=0.0)
    best_month_pnl_share = float(monthly_returns.max() / total_unit_pnl) if total_unit_pnl > 0.0 and not monthly_returns.empty else None
    winner_indices = returns[returns > 0].sort_values(ascending=False).index.tolist()

    def _summary_without_indices(excluded_indices: Sequence[int]) -> float:
        scoped = ordered.drop(index=list(excluded_indices), errors="ignore").copy()
        summary = _summarize_events(scoped, calendar_months=calendar_months)
        if summary is None:
            return 0.0
        return float(summary.get("annualized_unit_pnl_pct", 0.0) or 0.0)

    best_month = monthly_returns.idxmax() if not monthly_returns.empty else None
    annualized_remove_best_month = 0.0
    if best_month is not None:
        scoped_without_month = ordered[ordered["month_utc"].astype(str) != str(best_month)].copy()
        summary_without_month = _summarize_events(
            scoped_without_month,
            calendar_months=[month for month in calendar_months if month != best_month],
        )
        if summary_without_month is not None:
            annualized_remove_best_month = float(summary_without_month.get("annualized_unit_pnl_pct", 0.0) or 0.0)

    return {
        "top1_trade_pnl_share": _top_share(returns, total_unit_pnl, 1),
        "top3_trade_pnl_share": _top_share(returns, total_unit_pnl, 3),
        "top5_trade_pnl_share": _top_share(returns, total_unit_pnl, 5),
        "best_month_pnl_share": best_month_pnl_share,
        "annualized_remove_top1_trade_pct": _summary_without_indices(winner_indices[:1]),
        "annualized_remove_top3_trade_pct": _summary_without_indices(winner_indices[:3]),
        "annualized_remove_top5_trade_pct": _summary_without_indices(winner_indices[:5]),
        "annualized_remove_best_month_pct": annualized_remove_best_month,
    }


def _candidate_meets_goal(row: pd.Series | dict[str, object]) -> bool:
    return (
        float(row.get("trades_per_year", 0.0) or 0.0) >= _EDGE_MIN_TRADES_PER_YEAR
        and float(row.get("mean_return_pct", 0.0) or 0.0) >= _EDGE_MIN_MEAN_RETURN_PCT
        and float(row.get("win_rate", 0.0) or 0.0) >= _EDGE_MIN_WIN_RATE
        and float(row.get("annualized_unit_pnl_pct", 0.0) or 0.0) >= _EDGE_MIN_ANNUALIZED_UNIT_PNL_PCT
        and float(row.get("max_drawdown_pct", math.inf) or math.inf) <= _EDGE_MAX_DRAWDOWN_PCT
        and int(row.get("positive_months_count", 0) or 0) >= _EDGE_MIN_POSITIVE_MONTHS
        and int(row.get("equity_positive_months_count", 0) or 0) >= _EDGE_MIN_POSITIVE_MONTHS
    )


def _candidate_meets_distribution_goal(row: pd.Series | dict[str, object]) -> bool:
    top3_share = _safe_float(row.get("top3_trade_pnl_share"))
    top5_share = _safe_float(row.get("top5_trade_pnl_share"))
    best_month_share = _safe_float(row.get("best_month_pnl_share"))
    top3_symbol_share = _safe_float(row.get("top3_symbol_pnl_share"))
    top5_symbol_share = _safe_float(row.get("top5_symbol_pnl_share"))
    annualized_remove_top3 = float(row.get("annualized_remove_top3_trade_pct", 0.0) or 0.0)
    annualized_remove_top5 = float(row.get("annualized_remove_top5_trade_pct", 0.0) or 0.0)
    annualized_remove_best_month = float(row.get("annualized_remove_best_month_pct", 0.0) or 0.0)
    annualized_remove_top3_symbol = float(row.get("annualized_remove_top3_symbol_pct", 0.0) or 0.0)
    annualized_remove_top5_symbol = float(row.get("annualized_remove_top5_symbol_pct", 0.0) or 0.0)
    stable_positive_months = int(row.get("stable_positive_months_count", 0) or 0)
    equity_stable_positive_months = int(row.get("equity_stable_positive_months_count", 0) or 0)
    return (
        top3_share is not None
        and top5_share is not None
        and best_month_share is not None
        and top3_symbol_share is not None
        and top5_symbol_share is not None
        and top3_share <= _EDGE_MAX_TOP3_TRADE_SHARE
        and top5_share <= _EDGE_MAX_TOP5_TRADE_SHARE
        and best_month_share <= _EDGE_MAX_BEST_MONTH_SHARE
        and top3_symbol_share <= _EDGE_MAX_TOP3_SYMBOL_SHARE
        and top5_symbol_share <= _EDGE_MAX_TOP5_SYMBOL_SHARE
        and annualized_remove_top3 > 0.20
        and annualized_remove_top5 > 0.10
        and annualized_remove_best_month > 0.20
        and annualized_remove_top3_symbol > 0.20
        and annualized_remove_top5_symbol > 0.10
        and stable_positive_months >= _EDGE_MIN_STABLE_POSITIVE_MONTHS
        and equity_stable_positive_months >= _EDGE_MIN_STABLE_POSITIVE_MONTHS
    )


def _candidate_score(row: pd.Series | dict[str, object]) -> float:
    mean_return_pct = float(row.get("mean_return_pct", 0.0) or 0.0)
    median_return_pct = float(row.get("median_return_pct", 0.0) or 0.0)
    win_rate = float(row.get("win_rate", 0.0) or 0.0)
    trades_per_year = float(row.get("trades_per_year", 0.0) or 0.0)
    annualized = float(row.get("annualized_unit_pnl_pct", 0.0) or 0.0)
    max_drawdown = float(row.get("max_drawdown_pct", 1.0) or 1.0)
    positive_months = int(row.get("positive_months_count", 0) or 0)
    annualized_remove_top1 = float(row.get("annualized_remove_top1_trade_pct", 0.0) or 0.0)
    annualized_remove_top3 = float(row.get("annualized_remove_top3_trade_pct", 0.0) or 0.0)
    annualized_remove_top5 = float(row.get("annualized_remove_top5_trade_pct", 0.0) or 0.0)
    annualized_remove_best_month = float(row.get("annualized_remove_best_month_pct", 0.0) or 0.0)
    annualized_remove_top1_symbol = float(row.get("annualized_remove_top1_symbol_pct", 0.0) or 0.0)
    annualized_remove_top3_symbol = float(row.get("annualized_remove_top3_symbol_pct", 0.0) or 0.0)
    annualized_remove_top5_symbol = float(row.get("annualized_remove_top5_symbol_pct", 0.0) or 0.0)
    top1_share = float(_safe_float(row.get("top1_trade_pnl_share")) or 0.0)
    top3_share = float(_safe_float(row.get("top3_trade_pnl_share")) or 0.0)
    top5_share = float(_safe_float(row.get("top5_trade_pnl_share")) or 0.0)
    best_month_share = float(_safe_float(row.get("best_month_pnl_share")) or 0.0)
    top1_symbol_share = float(_safe_float(row.get("top1_symbol_pnl_share")) or 0.0)
    top3_symbol_share = float(_safe_float(row.get("top3_symbol_pnl_share")) or 0.0)
    top5_symbol_share = float(_safe_float(row.get("top5_symbol_pnl_share")) or 0.0)
    stable_positive_months = int(row.get("stable_positive_months_count", 0) or 0)
    equity_positive_months = int(row.get("equity_positive_months_count", 0) or 0)
    equity_stable_positive_months = int(row.get("equity_stable_positive_months_count", 0) or 0)
    median_positive_month_top1 = float(_safe_float(row.get("median_positive_month_top1_trade_share")) or 0.0)
    max_positive_month_top1 = float(_safe_float(row.get("max_positive_month_top1_trade_share")) or 0.0)
    equity_annualized = float(row.get("equity_annualized_return_pct", 0.0) or 0.0)
    equity_max_drawdown = float(row.get("equity_max_drawdown_pct", 1.0) or 1.0)
    equity_mean_trade = float(row.get("equity_mean_trade_pct", 0.0) or 0.0)
    components_count = int(row.get("components_count", 1) or 1)

    score = 0.0
    score += min(mean_return_pct, 0.06) * 1500.0
    score += min(max(median_return_pct, -0.02), 0.04) * 500.0
    score += min(win_rate, 0.70) * 250.0
    score += min(annualized, 3.5) * 55.0
    score += min(equity_annualized, 3.5) * 75.0
    score += min(equity_mean_trade, 0.04) * 1400.0
    score += min(trades_per_year, 150.0) * 0.20
    score += min(positive_months, 12) * 10.0
    score += min(stable_positive_months, 12) * 18.0
    score += min(equity_positive_months, 12) * 16.0
    score += min(equity_stable_positive_months, 12) * 24.0
    score += max(annualized_remove_top1, -1.0) * 15.0
    score += max(annualized_remove_top3, -1.0) * 35.0
    score += max(annualized_remove_top5, -1.0) * 20.0
    score += max(annualized_remove_best_month, -1.0) * 35.0
    score += max(annualized_remove_top1_symbol, -1.0) * 18.0
    score += max(annualized_remove_top3_symbol, -1.0) * 35.0
    score += max(annualized_remove_top5_symbol, -1.0) * 25.0
    score -= max(max_drawdown - 0.20, 0.0) * 220.0
    score -= max(equity_max_drawdown - 0.20, 0.0) * 280.0
    score -= top1_share * 20.0
    score -= top3_share * 45.0
    score -= top5_share * 20.0
    score -= best_month_share * 45.0
    score -= top1_symbol_share * 22.0
    score -= top3_symbol_share * 45.0
    score -= top5_symbol_share * 35.0
    score -= median_positive_month_top1 * 18.0
    score -= max_positive_month_top1 * 22.0
    score -= max(components_count - 1, 0) * 4.0
    if trades_per_year < _EDGE_MIN_TRADES_PER_YEAR:
        score -= (_EDGE_MIN_TRADES_PER_YEAR - trades_per_year) * 2.5
    if mean_return_pct < _EDGE_MIN_MEAN_RETURN_PCT:
        score -= (_EDGE_MIN_MEAN_RETURN_PCT - mean_return_pct) * 1800.0
    if median_return_pct < 0.0:
        score -= abs(median_return_pct) * 450.0
    if win_rate < _EDGE_MIN_WIN_RATE:
        score -= (_EDGE_MIN_WIN_RATE - win_rate) * 250.0
    if annualized < _EDGE_MIN_ANNUALIZED_UNIT_PNL_PCT:
        score -= (_EDGE_MIN_ANNUALIZED_UNIT_PNL_PCT - annualized) * 90.0
    if positive_months < _EDGE_MIN_POSITIVE_MONTHS:
        score -= (_EDGE_MIN_POSITIVE_MONTHS - positive_months) * 14.0
    if stable_positive_months < _EDGE_MIN_STABLE_POSITIVE_MONTHS:
        score -= (_EDGE_MIN_STABLE_POSITIVE_MONTHS - stable_positive_months) * 24.0
    if equity_positive_months < _EDGE_MIN_POSITIVE_MONTHS:
        score -= (_EDGE_MIN_POSITIVE_MONTHS - equity_positive_months) * 24.0
    if equity_stable_positive_months < _EDGE_MIN_STABLE_POSITIVE_MONTHS:
        score -= (_EDGE_MIN_STABLE_POSITIVE_MONTHS - equity_stable_positive_months) * 32.0
    if _candidate_meets_goal(row):
        score += 1_000.0
    if _candidate_meets_distribution_goal(row):
        score += 600.0
    return score


def _summarize_trade_frames(
    *,
    trade_events: pd.DataFrame,
    calendar_months: Sequence[str],
    group_columns: Sequence[str],
) -> pd.DataFrame:
    if trade_events.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for group_key, frame in trade_events.groupby(list(group_columns), sort=True):
        key_values = group_key if isinstance(group_key, tuple) else (group_key,)
        row = {column: value for column, value in zip(group_columns, key_values)}
        summary = _summarize_events(frame, calendar_months=calendar_months)
        if summary is None:
            continue
        row.update(summary)
        row.update(_build_concentration_metrics(frame, calendar_months=calendar_months))
        row.update(_build_month_stability_metrics(frame, calendar_months=calendar_months))
        row["stop_exit_rate"] = float((frame["exit_reason"].astype(str) == "stop").mean()) if not frame.empty else 0.0
        row["time_exit_rate"] = float((frame["exit_reason"].astype(str) == "time_exit").mean()) if not frame.empty else 0.0
        row["fast_fail_rate"] = float((frame["exit_reason"].astype(str) == "fast_fail").mean()) if not frame.empty else 0.0
        row["target_exit_rate"] = float((frame["exit_reason"].astype(str) == "target").mean()) if not frame.empty else 0.0
        row["mean_initial_risk_pct"] = float(pd.to_numeric(frame["initial_risk_pct"], errors="coerce").dropna().mean()) if "initial_risk_pct" in frame.columns else None
        row["mean_mfe_pct"] = float(pd.to_numeric(frame["max_return_after_entry_pct"], errors="coerce").dropna().mean()) if "max_return_after_entry_pct" in frame.columns else None
        row["median_hold_minutes"] = float(pd.to_numeric(frame["minutes_held_after_entry"], errors="coerce").dropna().median()) if "minutes_held_after_entry" in frame.columns else None
        row["components_count"] = 1
        row["meets_goal"] = _candidate_meets_goal(row)
        row["meets_distribution_goal"] = _candidate_meets_distribution_goal(row)
        row["selection_score"] = _candidate_score(row)
        rows.append(row)
    summary_frame = pd.DataFrame(rows)
    if summary_frame.empty:
        return summary_frame
    return _sort_summary_frame(
        summary_frame,
        sort_columns=["meets_distribution_goal", "meets_goal", "selection_score", "mean_return_pct", "annualized_unit_pnl_pct", "win_rate", "trades_per_year", "max_drawdown_pct"],
        ascending=[False, False, False, False, False, False, False, True],
    )


def _select_combo_candidates(atomic_summary: pd.DataFrame) -> pd.DataFrame:
    if atomic_summary.empty:
        return atomic_summary

    def _sort_existing(frame: pd.DataFrame, columns: Sequence[str], ascending: Sequence[bool]) -> pd.DataFrame:
        existing_columns = [column for column in columns if column in frame.columns]
        existing_ascending = [flag for column, flag in zip(columns, ascending, strict=False) if column in frame.columns]
        if not existing_columns:
            return frame
        return frame.sort_values(existing_columns, ascending=existing_ascending)

    eligible = atomic_summary[
        (pd.to_numeric(atomic_summary["trades_per_year"], errors="coerce").fillna(0.0) >= 15.0)
        & (pd.to_numeric(atomic_summary["annualized_unit_pnl_pct"], errors="coerce").fillna(0.0) > 0.0)
    ].copy()
    if eligible.empty:
        return atomic_summary.head(_EDGE_COMBO_CANDIDATE_LIMIT).copy()

    active = eligible[pd.to_numeric(eligible["trades_per_year"], errors="coerce").fillna(0.0) >= 40.0].copy()
    structure_active = eligible[pd.to_numeric(eligible["trades_per_year"], errors="coerce").fillna(0.0) >= 25.0].copy()
    quota_score = max(8, _EDGE_COMBO_CANDIDATE_LIMIT // 4)
    quota_mean = max(12, _EDGE_COMBO_CANDIDATE_LIMIT // 3)
    quota_active_mean = max(6, _EDGE_COMBO_CANDIDATE_LIMIT // 5)
    quota_months = max(4, _EDGE_COMBO_CANDIDATE_LIMIT // 6)
    quota_robust = max(4, _EDGE_COMBO_CANDIDATE_LIMIT // 6)
    quota_structure = max(8, _EDGE_COMBO_CANDIDATE_LIMIT // 4)
    quota_confirmed = max(8, _EDGE_COMBO_CANDIDATE_LIMIT // 4)

    structure_leaders = pd.DataFrame()
    if not structure_active.empty:
        structure_leaders = (
            structure_active.sort_values(
                ["entry_profile_id", "exit_profile_id", "mean_return_pct", "positive_months_count", "selection_score"],
                ascending=[True, True, False, False, False],
            )
            .groupby(["entry_profile_id", "exit_profile_id"], as_index=False)
            .head(1)
            .sort_values(
                ["mean_return_pct", "positive_months_count", "selection_score", "annualized_unit_pnl_pct"],
                ascending=[False, False, False, False],
            )
            .head(quota_structure)
        )

    confirmed_leaders = pd.DataFrame()
    confirmed_active = eligible[eligible["entry_profile_id"].astype(str).str.startswith("confirm_")].copy()
    if not confirmed_active.empty:
        confirmed_leaders = (
            _sort_existing(
                confirmed_active,
                [
                    "selection_score",
                    "positive_months_count",
                    "stable_positive_months_count",
                    "mean_return_pct",
                    "annualized_unit_pnl_pct",
                ],
                ascending=[False, False, False, False, False],
            )
            .head(quota_confirmed)
        )

    buckets = (
        eligible.head(quota_score),
        eligible.sort_values(
            ["mean_return_pct", "positive_months_count", "annualized_unit_pnl_pct"],
            ascending=[False, False, False],
        ).head(quota_mean),
        active.sort_values(
            ["mean_return_pct", "positive_months_count", "annualized_unit_pnl_pct"],
            ascending=[False, False, False],
        ).head(quota_active_mean),
        eligible.sort_values(
            ["positive_months_count", "annualized_remove_best_month_pct", "annualized_unit_pnl_pct"],
            ascending=[False, False, False],
        ).head(quota_months),
        eligible.sort_values(
            ["annualized_remove_top3_trade_pct", "annualized_remove_top5_trade_pct", "selection_score"],
            ascending=[False, False, False],
        ).head(quota_robust),
        structure_leaders,
        confirmed_leaders,
    )

    selected_rows: list[dict[str, object]] = []
    seen_ids: set[str] = set()

    def _add_rows(frame: pd.DataFrame) -> None:
        for row in frame.to_dict("records"):
            trade_model_id = str(row.get("trade_model_id", ""))
            if not trade_model_id or trade_model_id in seen_ids:
                continue
            selected_rows.append(row)
            seen_ids.add(trade_model_id)

    for bucket in buckets:
        _add_rows(bucket)

    if len(selected_rows) < _EDGE_COMBO_CANDIDATE_LIMIT:
        remaining = eligible.sort_values(
            ["selection_score", "positive_months_count", "mean_return_pct", "annualized_unit_pnl_pct"],
            ascending=[False, False, False, False],
        )
        _add_rows(remaining)

    return pd.DataFrame(selected_rows[:_EDGE_COMBO_CANDIDATE_LIMIT])


def _build_priority_selected_component_events(
    *,
    component_frames: dict[str, pd.DataFrame],
    component_ids: Sequence[str],
    component_priorities: dict[str, int],
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for component_id in component_ids:
        frame = component_frames.get(component_id)
        if frame is None or frame.empty:
            continue
        part = frame.copy()
        part["component_id"] = component_id
        part["component_priority"] = int(component_priorities[component_id])
        parts.append(part)
    if not parts:
        return pd.DataFrame()
    combined = pd.concat(parts, ignore_index=True).sort_values(
        ["symbol", "timestamp_ms", "component_priority", "entry_timestamp_ms"],
        ascending=[True, True, True, True],
        na_position="last",
    )
    rows: list[dict[str, object]] = []
    for (_, _), group in combined.groupby(["symbol", "timestamp_ms"], sort=True):
        best = group.iloc[0].copy()
        best["matched_component_count"] = int(len(group))
        best["matched_component_ids"] = ",".join(group["component_id"].astype(str).tolist())
        rows.append(best.to_dict())
    return pd.DataFrame(rows)


def _build_combo_summary(
    *,
    atomic_summary: pd.DataFrame,
    component_frames: dict[str, pd.DataFrame],
    calendar_months: Sequence[str],
    logger: logging.Logger,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    if atomic_summary.empty:
        return pd.DataFrame(), {}
    top_atomic = _select_combo_candidates(atomic_summary).copy().reset_index(drop=True)
    component_ids = top_atomic["trade_model_id"].astype(str).tolist()
    priority_by_component = {component_id: index + 1 for index, component_id in enumerate(component_ids)}
    total_combos = sum(math.comb(len(component_ids), size) for size in range(1, min(_EDGE_MAX_COMBO_COMPONENTS, len(component_ids)) + 1))
    processed = 0
    started_at = time.time()
    signature_best: dict[str, dict[str, object]] = {}
    combo_events_by_id: dict[str, pd.DataFrame] = {}

    for size in range(1, min(_EDGE_MAX_COMBO_COMPONENTS, len(component_ids)) + 1):
        for picked_ids in combinations(component_ids, size):
            processed += 1
            if processed == 1 or processed == total_combos or processed % 50 == 0:
                progress = processed / max(1, total_combos)
                elapsed = time.time() - started_at
                eta = (elapsed / progress - elapsed) if progress > 0 else None
                logger.info(
                    "unified-edge: stage=combos progress=%.1f%% processed=%s/%s elapsed=%s eta=%s найдено=%s",
                    progress * 100.0,
                    processed,
                    total_combos,
                    _format_duration(elapsed),
                    _format_duration(eta),
                    len(signature_best),
                )
            combo_events = _build_priority_selected_component_events(
                component_frames=component_frames,
                component_ids=picked_ids,
                component_priorities=priority_by_component,
            )
            if combo_events.empty:
                continue
            summary = _summarize_events(combo_events, calendar_months=calendar_months)
            if summary is None:
                continue
            component_slice = top_atomic[top_atomic["trade_model_id"].astype(str).isin(list(picked_ids))].copy()
            row = {
                "combo_id": hashlib.md5("|".join(picked_ids).encode("utf-8")).hexdigest()[:12],
                "component_ids": "|".join(picked_ids),
                "components_count": int(len(picked_ids)),
                "component_variants": ",".join(component_slice["atomic_variant"].astype(str).tolist()),
                "component_entry_profiles": ",".join(component_slice["entry_profile_id"].astype(str).tolist()),
                "component_exit_profiles": ",".join(component_slice["exit_profile_id"].astype(str).tolist()),
                "all_components_confirmed": bool(component_slice["entry_profile_id"].astype(str).str.startswith("confirm_").all()),
                "component_rule_texts": " || ".join(component_slice["rule_text"].astype(str).tolist()),
                **summary,
                **_build_concentration_metrics(combo_events, calendar_months=calendar_months),
                **_build_symbol_concentration_metrics(combo_events, calendar_months=calendar_months),
                **_build_month_stability_metrics(combo_events, calendar_months=calendar_months),
            }
            equity_metrics, _ = _simulate_equity_risk_metrics(combo_events, calendar_months=calendar_months)
            row.update(equity_metrics)
            row["meets_goal"] = _candidate_meets_goal(row)
            row["meets_distribution_goal"] = _candidate_meets_distribution_goal(row)
            row["selection_score"] = _candidate_score(row)
            signature = hashlib.md5(
                ",".join(
                    f"{symbol}|{int(timestamp_ms)}"
                    for symbol, timestamp_ms in combo_events[["symbol", "timestamp_ms"]].sort_values(["symbol", "timestamp_ms"]).itertuples(index=False)
                ).encode("utf-8")
            ).hexdigest()
            existing = signature_best.get(signature)
            if existing is None or float(row["selection_score"]) > float(existing["selection_score"]):
                signature_best[signature] = row
                combo_events_by_id[row["combo_id"]] = combo_events

    combo_summary = pd.DataFrame(signature_best.values())
    if combo_summary.empty:
        return combo_summary, {}
    combo_summary = _sort_summary_frame(
        combo_summary,
        sort_columns=["meets_distribution_goal", "meets_goal", "selection_score", "mean_return_pct", "annualized_unit_pnl_pct", "win_rate", "trades_per_year", "max_drawdown_pct"],
        ascending=[False, False, False, False, False, False, False, True],
    )
    combo_summary["combo_rank"] = range(1, len(combo_summary) + 1)
    combo_summary["combo_variant"] = [_index_to_variant_label(idx - 1) for idx in combo_summary["combo_rank"]]
    combo_summary["combo_priority"] = combo_summary["combo_rank"]
    return combo_summary, combo_events_by_id


def _build_risk_ladder_frame(
    *,
    events: pd.DataFrame,
    calendar_months: Sequence[str],
    risk_levels: Sequence[float] = (0.03, 0.04, 0.05),
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for risk_fraction in risk_levels:
        metrics, _ = _simulate_equity_risk_metrics(
            events,
            calendar_months=calendar_months,
            risk_fraction=float(risk_fraction),
        )
        rows.append(
            {
                "risk_fraction": float(risk_fraction),
                "risk_pct": float(risk_fraction * 100.0),
                "risk_label": f"{risk_fraction * 100.0:.0f}%",
                **metrics,
            }
        )
    return pd.DataFrame(rows)


def _build_holdout_summary(*, events: pd.DataFrame, calendar_months: Sequence[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for split_id, train_months in _EDGE_HOLDOUT_SPLITS:
        if len(calendar_months) <= train_months:
            continue
        test_months = list(calendar_months[train_months:])
        scoped = events[events["month_utc"].astype(str).isin(test_months)].copy()
        summary = _summarize_events(scoped, calendar_months=test_months)
        if summary is None:
            continue
        equity_metrics, _ = _simulate_equity_risk_metrics(scoped, calendar_months=test_months)
        rows.append(
            {
                "split_id": split_id,
                "train_months": train_months,
                "test_months": len(test_months),
                "test_month_list": ",".join(test_months),
                **summary,
                **_build_concentration_metrics(scoped, calendar_months=test_months),
                **_build_symbol_concentration_metrics(scoped, calendar_months=test_months),
                **_build_month_stability_metrics(scoped, calendar_months=test_months),
                **equity_metrics,
            }
        )
    return pd.DataFrame(rows)


def _build_top_trade_charts(
    *,
    events: pd.DataFrame,
    preparer: DataPreparer,
    charts_dir: Path,
    logger: logging.Logger,
    top_n: int = 10,
) -> dict[str, Path]:
    winners_dir = charts_dir / "top_winners"
    losers_dir = charts_dir / "top_losers"
    winners_dir.mkdir(parents=True, exist_ok=True)
    losers_dir.mkdir(parents=True, exist_ok=True)
    _clear_png_files(winners_dir)
    _clear_png_files(losers_dir)

    plotter = StaticComboTradePlotter()
    frame_cache: dict[tuple[str, str], pd.DataFrame] = {}

    def _render_subset(*, ordered: pd.DataFrame, target_dir: Path, prefix: str) -> pd.DataFrame:
        manifest_rows: list[dict[str, object]] = []
        total = len(ordered)
        if total == 0:
            return pd.DataFrame(
                columns=[
                    "chart_rank",
                    "symbol",
                    "trade_model_label",
                    "entry_timestamp_utc",
                    "exit_return_pct",
                    "chart_status",
                    "chart_path",
                ]
            )

        started_at = time.time()
        for idx, (_, event) in enumerate(ordered.iterrows(), start=1):
            symbol = str(event.get("symbol", ""))
            timeframe = Timeframe(str(event.get("timeframe", "5m")))
            cache_key = (symbol, timeframe.value)
            frame = frame_cache.get(cache_key)
            if frame is None:
                frame = preparer.load_symbol_data(symbol, timeframe)
                frame_cache[cache_key] = frame

            chart_status = "создан"
            chart_path: Path | None = None
            if frame.empty:
                chart_status = "нет_данных_в_кеше"
            else:
                trigger_timestamp_ms = int(_safe_float(event.get("timestamp_ms")) or 0)
                entry_timestamp_ms = int(_safe_float(event.get("entry_timestamp_ms")) or trigger_timestamp_ms)
                exit_timestamp_raw = _safe_float(event.get("exit_timestamp_ms"))
                exit_timestamp_ms = int(exit_timestamp_raw) if exit_timestamp_raw is not None else None
                timeframe_ms = timeframe.to_milliseconds()
                start_ts = int(trigger_timestamp_ms - (timeframe_ms * 18))
                right_anchor = exit_timestamp_ms if exit_timestamp_ms is not None else entry_timestamp_ms + (timeframe_ms * 24)
                end_ts = int(right_anchor + (timeframe_ms * 18))
                scoped = frame[
                    (pd.to_numeric(frame["timestamp"], errors="coerce") >= start_ts)
                    & (pd.to_numeric(frame["timestamp"], errors="coerce") <= end_ts)
                ].copy()
                if scoped.empty:
                    chart_status = "пустое_окно_графика"
                else:
                    chart_name = (
                        f"{idx:02d}_{prefix}_{_sanitize_filename(symbol)}_"
                        f"{pd.to_datetime(entry_timestamp_ms, unit='ms', utc=True).strftime('%Y%m%d_%H%M')}.png"
                    )
                    chart_path = target_dir / chart_name
                    try:
                        plotter.plot_trade(
                            frame=scoped,
                            spec=StaticComboTradePlotSpec(
                                symbol=symbol,
                                combo_variant=str(event.get("combo_variant") or "best"),
                                component_ids=str(event.get("matched_component_ids") or event.get("component_id") or ""),
                                trigger_timestamp_ms=trigger_timestamp_ms,
                                entry_timestamp_ms=entry_timestamp_ms,
                                exit_timestamp_ms=exit_timestamp_ms,
                                entry_price=_safe_float(event.get("entry_price")),
                                stop_price=_safe_float(event.get("initial_stop_price")),
                                exit_price=None,
                                trigger_open=_safe_float(event.get("trigger_open")),
                                trigger_high=_safe_float(event.get("trigger_high")),
                                trigger_low=_safe_float(event.get("trigger_low")),
                                trigger_close=_safe_float(event.get("trigger_close")),
                                trigger_return_pct=_safe_float(event.get("trigger_return_pct")),
                                trigger_range_pct=_safe_float(event.get("trigger_range_pct")),
                                range_atr=_safe_float(event.get("range_atr")),
                                body_atr=_safe_float(event.get("body_atr")),
                                volume_mult=_safe_float(event.get("volume_mult")),
                                close_to_high_frac=_safe_float(event.get("close_to_high_frac")),
                                pre_base_range_pct_60m=_safe_float(event.get("pre_base_range_pct_60m")),
                                pre_base_drift_pct_60m=_safe_float(event.get("pre_base_drift_pct_60m")),
                                pre_base_range_vs_trigger=_safe_float(event.get("pre_base_range_vs_trigger")),
                                pre_entry_pullback_frac=_safe_float(event.get("pre_entry_pullback_frac")),
                                pre_entry_red_volume_frac=_safe_float(event.get("pre_entry_red_volume_frac")),
                                next_bar_pullback_frac=_safe_float(event.get("next_bar_pullback_frac")),
                                next_close_to_high_frac=None,
                                initial_risk_pct=_safe_float(event.get("initial_risk_pct")),
                                peak_timestamp_ms=int(_safe_float(event.get("peak_timestamp_ms"))) if _safe_float(event.get("peak_timestamp_ms")) is not None else None,
                                peak_price=_safe_float(event.get("peak_price_before_50pct_retrace")),
                                exit_return_pct=_safe_float(event.get("exit_return_pct")),
                                exit_reason=str(event.get("exit_reason", "")) or None,
                                entry_reason=str(event.get("entry_reason", "")) or None,
                                initial_stop_reason=str(event.get("initial_stop_reason", "")) or None,
                                source_trade_model_id=str(event.get("trade_model_id", "")) or None,
                                source_trade_model_label=str(event.get("trade_model_label", "")) or None,
                                source_config_id=str(event.get("component_id", "")) or None,
                                hour_utc=int(_safe_float(event.get("hour_utc"))) if _safe_float(event.get("hour_utc")) is not None else None,
                            ),
                            output_path=chart_path,
                        )
                    except Exception as exc:
                        chart_status = f"ошибка_графика:{type(exc).__name__}"
                        chart_path = None
                        logger.warning(
                            "unified-edge: stage=trade-charts warning kind=%s symbol=%s reason=%s",
                            prefix,
                            symbol,
                            exc,
                        )

            manifest_rows.append(
                {
                    "chart_rank": idx,
                    "symbol": symbol,
                    "trade_model_label": event.get("trade_model_label"),
                    "entry_timestamp_utc": event.get("entry_timestamp_utc"),
                    "exit_return_pct": _safe_float(event.get("exit_return_pct")),
                    "chart_status": chart_status,
                    "chart_path": str(chart_path) if chart_path is not None else None,
                }
            )

            if idx == 1 or idx == total or idx % 5 == 0:
                progress_pct, elapsed, eta_seconds = _progress_snapshot(completed=idx, total=total, started_at=started_at)
                logger.info(
                    "unified-edge: stage=trade-charts kind=%s progress=%.1f%% charts=%s/%s elapsed=%s eta=%s",
                    prefix,
                    progress_pct,
                    idx,
                    total,
                    _format_duration(elapsed),
                    _format_duration(eta_seconds),
                )

        return pd.DataFrame(manifest_rows)

    scoped = events.dropna(subset=["exit_return_pct"]).copy()
    winners_ordered = scoped.sort_values(["exit_return_pct", "entry_timestamp_ms"], ascending=[False, True]).head(top_n).reset_index(drop=True)
    losers_ordered = scoped.sort_values(["exit_return_pct", "entry_timestamp_ms"], ascending=[True, True]).head(top_n).reset_index(drop=True)

    winners_manifest = _render_subset(ordered=winners_ordered, target_dir=winners_dir, prefix="winner")
    losers_manifest = _render_subset(ordered=losers_ordered, target_dir=losers_dir, prefix="loser")

    winners_manifest_path = charts_dir / "top_winners_manifest.csv"
    losers_manifest_path = charts_dir / "top_losers_manifest.csv"
    winners_manifest.to_csv(winners_manifest_path, index=False)
    losers_manifest.to_csv(losers_manifest_path, index=False)
    return {
        "top_winners_dir": winners_dir,
        "top_losers_dir": losers_dir,
        "top_winners_manifest": winners_manifest_path,
        "top_losers_manifest": losers_manifest_path,
    }


def _build_report(
    *,
    report_path: Path,
    context: dict[str, object],
    atomic_summary: pd.DataFrame,
    best_summary: pd.DataFrame,
    best_monthly: pd.DataFrame,
    best_holdout: pd.DataFrame,
    behavior_by_entry: pd.DataFrame,
    behavior_by_stop: pd.DataFrame,
    behavior_by_trail: pd.DataFrame,
    behavior_by_exit: pd.DataFrame,
) -> None:
    best_row = best_summary.iloc[0].to_dict() if not best_summary.empty else {}
    lines = [
        "# Единый поиск edge для XX:00",
        "",
        "Отчёт по единому поиску edge без развилок по часу. Одинаковые правила применяются ко всем XX:00-сигналам.",
        "",
        "## Лучший итоговый вариант",
        "",
        _frame_to_markdown(best_summary, columns=["combo_variant", "components_count", "component_variants", "trades_per_year", "mean_return_pct", "median_return_pct", "win_rate", "annualized_unit_pnl_pct", "max_drawdown_pct", "positive_months_count", "top3_trade_pnl_share", "top5_trade_pnl_share", "best_month_pnl_share", "annualized_remove_top3_trade_pct", "annualized_remove_top5_trade_pct", "annualized_remove_best_month_pct"]),
        "",
        "## Правила лучшего варианта",
        "",
        f"- Состав: `{best_row.get('component_variants', '')}`",
        f"- Компоненты: `{best_row.get('component_rule_texts', '')}`",
        "",
        "## Лучшие атомарные модели",
        "",
        _frame_to_markdown(atomic_summary, columns=["atomic_variant", "signal_profile_id", "entry_profile_id", "exit_profile_id", "trades_per_year", "mean_return_pct", "median_return_pct", "win_rate", "annualized_unit_pnl_pct", "max_drawdown_pct", "positive_months_count", "top3_trade_pnl_share", "best_month_pnl_share"], limit=15),
        "",
        "## Что дали разные входы",
        "",
        _frame_to_markdown(behavior_by_entry, columns=["entry_profile_id", "models_count", "mean_of_mean_return_pct", "median_of_mean_return_pct", "mean_of_win_rate", "mean_of_annualized_unit_pnl_pct", "mean_of_max_drawdown_pct", "mean_of_top3_trade_pnl_share"]),
        "",
        "## Что дали разные типы начального стопа",
        "",
        _frame_to_markdown(behavior_by_stop, columns=["initial_stop_style", "models_count", "mean_of_mean_return_pct", "median_of_mean_return_pct", "mean_of_win_rate", "mean_of_annualized_unit_pnl_pct", "mean_of_max_drawdown_pct", "mean_of_top3_trade_pnl_share"]),
        "",
        "## Что дали разные варианты трейлинга",
        "",
        _frame_to_markdown(behavior_by_trail, columns=["trail_style", "models_count", "mean_of_mean_return_pct", "median_of_mean_return_pct", "mean_of_win_rate", "mean_of_annualized_unit_pnl_pct", "mean_of_max_drawdown_pct", "mean_of_top3_trade_pnl_share"]),
        "",
        "## Что дали разные варианты сопровождения",
        "",
        _frame_to_markdown(behavior_by_exit, columns=["exit_profile_id", "models_count", "mean_of_mean_return_pct", "median_of_mean_return_pct", "mean_of_win_rate", "mean_of_annualized_unit_pnl_pct", "mean_of_max_drawdown_pct", "mean_of_top3_trade_pnl_share"]),
        "",
        "## Месяцы лучшего варианта",
        "",
        _frame_to_markdown(best_monthly, columns=["month_utc", "trades_count", "month_return_pct", "win_rate", "mean_return_pct"]),
        "",
        "## Позднее sanity-окно",
        "",
        _frame_to_markdown(best_holdout, columns=["split_id", "trades_count", "trades_per_year", "mean_return_pct", "win_rate", "annualized_unit_pnl_pct", "max_drawdown_pct", "positive_months_count", "top3_trade_pnl_share", "best_month_pnl_share"]),
        "",
        "## Контекст",
        "",
        "```json",
        json.dumps(context, ensure_ascii=False, indent=2),
        "```",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _build_report_v2(
    *,
    report_path: Path,
    context: dict[str, object],
    atomic_summary: pd.DataFrame,
    best_summary: pd.DataFrame,
    best_monthly: pd.DataFrame,
    best_holdout: pd.DataFrame,
    best_risk_ladder: pd.DataFrame,
    confirmed_best_summary: pd.DataFrame,
    confirmed_best_monthly: pd.DataFrame,
    confirmed_best_holdout: pd.DataFrame,
    confirmed_best_risk_ladder: pd.DataFrame,
    behavior_by_entry: pd.DataFrame,
    behavior_by_stop: pd.DataFrame,
    behavior_by_trail: pd.DataFrame,
    behavior_by_exit: pd.DataFrame,
) -> None:
    best_row = best_summary.iloc[0].to_dict() if not best_summary.empty else {}
    confirmed_row = confirmed_best_summary.iloc[0].to_dict() if not confirmed_best_summary.empty else {}
    lines = [
        "# Единый поиск edge для XX:00",
        "",
        "Отчёт по единому поиску edge без развилок по часу. Одинаковые правила применяются ко всем XX:00-сигналам.",
        "",
        "## Лучший итоговый вариант",
        "",
        _frame_to_markdown(
            best_summary,
            columns=[
                "combo_variant",
                "components_count",
                "component_variants",
                "trades_per_year",
                "mean_return_pct",
                "median_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "stable_positive_months_count",
                "equity_positive_months_count",
                "equity_stable_positive_months_count",
                "equity_annualized_return_pct",
                "equity_max_drawdown_pct",
                "equity_mean_trade_pct",
                "top3_trade_pnl_share",
                "top5_trade_pnl_share",
                "top3_symbol_pnl_share",
                "top5_symbol_pnl_share",
            ],
        ),
        "",
        "## Правила лучшего варианта",
        "",
        f"- Состав: `{best_row.get('component_variants', '')}`",
        f"- Компоненты: `{best_row.get('component_rule_texts', '')}`",
        "",
        "## Капитал и риск лучшего варианта",
        "",
        _frame_to_markdown(
            best_summary,
            columns=[
                "equity_risk_fraction",
                "equity_total_return_pct",
                "equity_annualized_return_pct",
                "equity_final_equity",
                "equity_max_drawdown_pct",
                "equity_mean_trade_pct",
                "equity_mean_pos_trade_pct",
                "equity_mean_neg_trade_pct",
                "equity_positive_months_count",
                "equity_non_positive_months_count",
                "equity_stable_positive_months_count",
                "equity_fragile_positive_months_count",
                "equity_max_notional_fraction",
                "equity_max_open_positions",
            ],
        ),
        "",
        "### Лестница риска 3% / 4% / 5%",
        "",
        _frame_to_markdown(
            best_risk_ladder,
            columns=[
                "risk_label",
                "equity_total_return_pct",
                "equity_annualized_return_pct",
                "equity_max_drawdown_pct",
                "equity_mean_trade_pct",
                "equity_mean_pos_trade_pct",
                "equity_mean_neg_trade_pct",
                "equity_positive_months_count",
                "equity_non_positive_months_count",
                "equity_stable_positive_months_count",
                "equity_fragile_positive_months_count",
                "equity_max_notional_fraction",
                "equity_max_open_positions",
            ],
        ),
        "",
        "## Лучший confirmed-стек",
        "",
        _frame_to_markdown(
            confirmed_best_summary,
            columns=[
                "combo_variant",
                "components_count",
                "component_variants",
                "trades_per_year",
                "mean_return_pct",
                "median_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "max_drawdown_pct",
                "positive_months_count",
                "stable_positive_months_count",
                "equity_positive_months_count",
                "equity_stable_positive_months_count",
                "equity_annualized_return_pct",
                "equity_max_drawdown_pct",
                "equity_mean_trade_pct",
                "top3_trade_pnl_share",
                "top5_trade_pnl_share",
                "top3_symbol_pnl_share",
                "top5_symbol_pnl_share",
            ],
        ),
        "",
        "### Правила лучшего confirmed-стека",
        "",
        f"- Состав: `{confirmed_row.get('component_variants', '')}`",
        f"- Компоненты: `{confirmed_row.get('component_rule_texts', '')}`",
        "",
        "### Капитал и риск лучшего confirmed-стека",
        "",
        _frame_to_markdown(
            confirmed_best_summary,
            columns=[
                "equity_risk_fraction",
                "equity_total_return_pct",
                "equity_annualized_return_pct",
                "equity_final_equity",
                "equity_max_drawdown_pct",
                "equity_mean_trade_pct",
                "equity_mean_pos_trade_pct",
                "equity_mean_neg_trade_pct",
                "equity_positive_months_count",
                "equity_non_positive_months_count",
                "equity_stable_positive_months_count",
                "equity_fragile_positive_months_count",
                "equity_max_notional_fraction",
                "equity_max_open_positions",
            ],
        ),
        "",
        "### Лестница риска 3% / 4% / 5% для confirmed-стека",
        "",
        _frame_to_markdown(
            confirmed_best_risk_ladder,
            columns=[
                "risk_label",
                "equity_total_return_pct",
                "equity_annualized_return_pct",
                "equity_max_drawdown_pct",
                "equity_mean_trade_pct",
                "equity_mean_pos_trade_pct",
                "equity_mean_neg_trade_pct",
                "equity_positive_months_count",
                "equity_non_positive_months_count",
                "equity_max_notional_fraction",
                "equity_max_open_positions",
            ],
        ),
        "",
        "## Лучшие атомарные модели",
        "",
        _frame_to_markdown(
            atomic_summary,
            columns=[
                "atomic_variant",
                "signal_profile_id",
                "entry_profile_id",
                "exit_profile_id",
                "trades_per_year",
                "mean_return_pct",
                "median_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "equity_annualized_return_pct",
                "equity_max_drawdown_pct",
                "stable_positive_months_count",
                "top3_trade_pnl_share",
                "top3_symbol_pnl_share",
            ],
            limit=15,
        ),
        "",
        "## Что дали разные входы",
        "",
        _frame_to_markdown(
            behavior_by_entry,
            columns=[
                "entry_profile_id",
                "models_count",
                "mean_of_mean_return_pct",
                "median_of_mean_return_pct",
                "mean_of_win_rate",
                "mean_of_annualized_unit_pnl_pct",
                "mean_of_max_drawdown_pct",
                "mean_of_top3_trade_pnl_share",
            ],
        ),
        "",
        "## Что дали разные типы начального стопа",
        "",
        _frame_to_markdown(
            behavior_by_stop,
            columns=[
                "initial_stop_style",
                "models_count",
                "mean_of_mean_return_pct",
                "median_of_mean_return_pct",
                "mean_of_win_rate",
                "mean_of_annualized_unit_pnl_pct",
                "mean_of_max_drawdown_pct",
                "mean_of_top3_trade_pnl_share",
            ],
        ),
        "",
        "## Что дали разные варианты трейлинга",
        "",
        _frame_to_markdown(
            behavior_by_trail,
            columns=[
                "trail_style",
                "models_count",
                "mean_of_mean_return_pct",
                "median_of_mean_return_pct",
                "mean_of_win_rate",
                "mean_of_annualized_unit_pnl_pct",
                "mean_of_max_drawdown_pct",
                "mean_of_top3_trade_pnl_share",
            ],
        ),
        "",
        "## Что дали разные варианты сопровождения",
        "",
        _frame_to_markdown(
            behavior_by_exit,
            columns=[
                "exit_profile_id",
                "models_count",
                "mean_of_mean_return_pct",
                "median_of_mean_return_pct",
                "mean_of_win_rate",
                "mean_of_annualized_unit_pnl_pct",
                "mean_of_max_drawdown_pct",
                "mean_of_top3_trade_pnl_share",
            ],
        ),
        "",
        "## Месяцы лучшего варианта",
        "",
        _frame_to_markdown(
            best_monthly,
            columns=[
                "month_utc",
                "trades_count",
                "total_return_pct",
                "month_positive",
                "equity_month_pnl_pct",
                "equity_win_rate",
                "equity_mean_trade_pct",
                "wins_count",
                "stable_positive_month",
                "top1_positive_trade_share",
                "top2_positive_trade_share",
            ],
        ),
        "",
        "## Месяцы лучшего confirmed-стека",
        "",
        _frame_to_markdown(
            confirmed_best_monthly,
            columns=[
                "month_utc",
                "trades_count",
                "total_return_pct",
                "month_positive",
                "equity_month_pnl_pct",
                "equity_win_rate",
                "equity_mean_trade_pct",
                "wins_count",
                "stable_positive_month",
                "top1_positive_trade_share",
                "top2_positive_trade_share",
            ],
        ),
        "",
        "## Позднее sanity-окно",
        "",
        _frame_to_markdown(
            best_holdout,
            columns=[
                "split_id",
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "equity_annualized_return_pct",
                "equity_max_drawdown_pct",
                "positive_months_count",
                "stable_positive_months_count",
                "top3_trade_pnl_share",
                "top3_symbol_pnl_share",
                "best_month_pnl_share",
            ],
        ),
        "",
        "## Позднее sanity-окно лучшего confirmed-стека",
        "",
        _frame_to_markdown(
            confirmed_best_holdout,
            columns=[
                "split_id",
                "trades_count",
                "trades_per_year",
                "mean_return_pct",
                "win_rate",
                "annualized_unit_pnl_pct",
                "equity_annualized_return_pct",
                "equity_max_drawdown_pct",
                "positive_months_count",
                "stable_positive_months_count",
                "top3_trade_pnl_share",
                "top3_symbol_pnl_share",
                "best_month_pnl_share",
            ],
        ),
        "",
        "## Контекст",
        "",
        "```json",
        json.dumps(context, ensure_ascii=False, indent=2),
        "```",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def build_hourly_asia_pump_unified_edge_artifacts(
    *,
    base_events_path: Path,
    output_dir: Path,
    cache_dir: Path | None = None,
    commission_rate: float = 0.0,
    preparer: DataPreparer | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    active_logger = logger or module_logger
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_events = _prepare_selected_events(base_events_path)
    if selected_events.empty:
        raise ValueError(f"Не найдены базовые события в {base_events_path}")
    timeframe = Timeframe(str(selected_events["timeframe"].dropna().astype(str).iloc[0]))
    calendar_months = _calendar_months_from_frames(selected_events)
    active_preparer = preparer or DataPreparer(Path(cache_dir))
    models = _build_execution_models()
    active_logger.info("unified-edge: stage=init events=%s timeframe=%s models=%s output_dir=%s", len(selected_events), timeframe.value, len(models), output_dir)

    trade_events = _simulate_unified_execution_events(
        preparer=active_preparer,
        timeframe=timeframe,
        selected_events=selected_events,
        models=models,
        commission_rate=commission_rate,
        logger=active_logger,
    )
    if trade_events.empty:
        raise ValueError("Не удалось построить ни одной сделки в unified-edge search")

    atomic_summary = _summarize_trade_frames(
        trade_events=trade_events,
        calendar_months=calendar_months,
        group_columns=["trade_model_id", "trade_model_label", "signal_profile_id", "entry_profile_id", "exit_profile_id", "initial_stop_style", "trail_style", "partial_fraction", "partial_take_pct", "breakeven_activation_pct", "max_hold_minutes", "rule_text"],
    )
    atomic_summary["atomic_rank"] = range(1, len(atomic_summary) + 1)
    atomic_summary["atomic_variant"] = [_index_to_variant_label(idx - 1) for idx in atomic_summary["atomic_rank"]]
    component_frames = {model_id: frame.copy().reset_index(drop=True) for model_id, frame in trade_events.groupby("trade_model_id", sort=True)}
    combo_summary, combo_events_by_id = _build_combo_summary(atomic_summary=atomic_summary, component_frames=component_frames, calendar_months=calendar_months, logger=active_logger)
    if combo_summary.empty:
        raise ValueError("Не удалось собрать ни одной комбинации unified-edge")

    def _build_monthly_stability_frame(events: pd.DataFrame) -> pd.DataFrame:
        monthly = _build_monthly_returns_frame(events, calendar_months=calendar_months)
        _, monthly_equity = _simulate_equity_risk_metrics(events, calendar_months=calendar_months)
        monthly_stability = pd.merge(
            monthly,
            monthly_equity,
            on="month_utc",
            how="left",
        )
        if monthly_stability.empty:
            return monthly_stability
        month_details_rows: list[dict[str, object]] = []
        for month in calendar_months:
            month_frame = events[events["month_utc"].astype(str) == str(month)].copy()
            returns = pd.to_numeric(month_frame.get("exit_return_pct"), errors="coerce").fillna(0.0)
            wins = int((returns > 0).sum())
            trades = int(len(month_frame))
            month_pnl = float(returns.sum()) if trades > 0 else 0.0
            win_rate = float(wins / trades) if trades > 0 else 0.0
            positive_returns = returns[returns > 0].sort_values(ascending=False)
            top1_share = float(positive_returns.head(1).sum() / month_pnl) if month_pnl > 0.0 and not positive_returns.empty else None
            top2_share = float(positive_returns.head(2).sum() / month_pnl) if month_pnl > 0.0 and not positive_returns.empty else None
            month_details_rows.append(
                {
                    "month_utc": str(month),
                    "wins_count": wins,
                    "stable_positive_month": bool(month_pnl > 0.0 and wins >= 2 and win_rate >= 0.50),
                    "top1_positive_trade_share": top1_share,
                    "top2_positive_trade_share": top2_share,
                }
            )
        return monthly_stability.merge(pd.DataFrame(month_details_rows), on="month_utc", how="left")

    best_summary = combo_summary.head(1).copy()
    best_combo_id = str(best_summary.iloc[0]["combo_id"])
    best_events = combo_events_by_id[best_combo_id].copy().reset_index(drop=True)
    best_equity_metrics, best_monthly_equity = _simulate_equity_risk_metrics(best_events, calendar_months=calendar_months)
    best_monthly = _build_monthly_returns_frame(best_events, calendar_months=calendar_months)
    best_monthly_stability = _build_monthly_stability_frame(best_events)
    best_holdout = _build_holdout_summary(events=best_events, calendar_months=calendar_months)
    best_risk_ladder = _build_risk_ladder_frame(events=best_events, calendar_months=calendar_months)

    confirmed_combo_summary = combo_summary[combo_summary["all_components_confirmed"].astype(bool)].copy().reset_index(drop=True)
    if confirmed_combo_summary.empty:
        confirmed_best_summary = pd.DataFrame(columns=combo_summary.columns)
        confirmed_best_events = pd.DataFrame(columns=best_events.columns)
        confirmed_best_monthly = pd.DataFrame(columns=best_monthly.columns)
        confirmed_best_holdout = pd.DataFrame(columns=best_holdout.columns)
        confirmed_best_risk_ladder = pd.DataFrame(columns=best_risk_ladder.columns)
        confirmed_best_monthly_stability = pd.DataFrame(columns=best_monthly_stability.columns)
    else:
        confirmed_best_summary = confirmed_combo_summary.head(1).copy()
        confirmed_best_combo_id = str(confirmed_best_summary.iloc[0]["combo_id"])
        confirmed_best_events = combo_events_by_id[confirmed_best_combo_id].copy().reset_index(drop=True)
        confirmed_best_monthly = _build_monthly_returns_frame(confirmed_best_events, calendar_months=calendar_months)
        confirmed_best_monthly_stability = _build_monthly_stability_frame(confirmed_best_events)
        confirmed_best_holdout = _build_holdout_summary(events=confirmed_best_events, calendar_months=calendar_months)
        confirmed_best_risk_ladder = _build_risk_ladder_frame(events=confirmed_best_events, calendar_months=calendar_months)

    behavior_by_entry = atomic_summary.groupby("entry_profile_id", sort=True).agg(models_count=("trade_model_id", "count"), mean_of_mean_return_pct=("mean_return_pct", "mean"), median_of_mean_return_pct=("mean_return_pct", "median"), mean_of_win_rate=("win_rate", "mean"), mean_of_annualized_unit_pnl_pct=("annualized_unit_pnl_pct", "mean"), mean_of_max_drawdown_pct=("max_drawdown_pct", "mean"), mean_of_top3_trade_pnl_share=("top3_trade_pnl_share", "mean")).reset_index().sort_values("mean_of_mean_return_pct", ascending=False).reset_index(drop=True)
    behavior_by_stop = atomic_summary.groupby("initial_stop_style", sort=True).agg(models_count=("trade_model_id", "count"), mean_of_mean_return_pct=("mean_return_pct", "mean"), median_of_mean_return_pct=("mean_return_pct", "median"), mean_of_win_rate=("win_rate", "mean"), mean_of_annualized_unit_pnl_pct=("annualized_unit_pnl_pct", "mean"), mean_of_max_drawdown_pct=("max_drawdown_pct", "mean"), mean_of_top3_trade_pnl_share=("top3_trade_pnl_share", "mean")).reset_index().sort_values("mean_of_mean_return_pct", ascending=False).reset_index(drop=True)
    behavior_by_trail = atomic_summary.groupby("trail_style", sort=True).agg(models_count=("trade_model_id", "count"), mean_of_mean_return_pct=("mean_return_pct", "mean"), median_of_mean_return_pct=("mean_return_pct", "median"), mean_of_win_rate=("win_rate", "mean"), mean_of_annualized_unit_pnl_pct=("annualized_unit_pnl_pct", "mean"), mean_of_max_drawdown_pct=("max_drawdown_pct", "mean"), mean_of_top3_trade_pnl_share=("top3_trade_pnl_share", "mean")).reset_index().sort_values("mean_of_mean_return_pct", ascending=False).reset_index(drop=True)
    behavior_by_exit = atomic_summary.groupby("exit_profile_id", sort=True).agg(models_count=("trade_model_id", "count"), mean_of_mean_return_pct=("mean_return_pct", "mean"), median_of_mean_return_pct=("mean_return_pct", "median"), mean_of_win_rate=("win_rate", "mean"), mean_of_annualized_unit_pnl_pct=("annualized_unit_pnl_pct", "mean"), mean_of_max_drawdown_pct=("max_drawdown_pct", "mean"), mean_of_top3_trade_pnl_share=("top3_trade_pnl_share", "mean")).reset_index().sort_values("mean_of_mean_return_pct", ascending=False).reset_index(drop=True)

    charts_dir = output_dir / "unified_edge_charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    _save_priority_equity_curve_chart(best_events, charts_dir / "equity_curve.png")
    _save_priority_monthly_returns_chart(best_monthly, charts_dir / "monthly_returns.png")
    _save_priority_trade_distribution_chart(best_events, charts_dir / "trade_distribution.png")
    _save_priority_trade_timeline_chart(best_events, charts_dir / "trade_timeline.png")
    top_trade_chart_artifacts = _build_top_trade_charts(
        events=best_events,
        preparer=active_preparer,
        charts_dir=charts_dir,
        logger=active_logger,
        top_n=10,
    )

    context = {
        "search_scope": {
            "uses_hour_utc_in_optimization": False,
            "same_rules_for_all_xx00": True,
            "timeframe": timeframe.value,
            "events_count": int(len(selected_events)),
            "atomic_models_count": int(len(models)),
            "commission_rate": float(commission_rate),
            "round_trip_taker_fee_pct": float(commission_rate * 2.0),
            "equity_risk_fraction": float(_EDGE_EQUITY_RISK_FRACTION),
            "equity_max_total_notional_fraction": float(_EDGE_EQUITY_MAX_NOTIONAL_FRACTION),
            "risk_ladder_levels": [0.03, 0.04, 0.05],
        },
        "criteria": {
            "min_trades_per_year": _EDGE_MIN_TRADES_PER_YEAR,
            "min_mean_return_pct": _EDGE_MIN_MEAN_RETURN_PCT,
            "min_win_rate": _EDGE_MIN_WIN_RATE,
            "min_annualized_unit_pnl_pct": _EDGE_MIN_ANNUALIZED_UNIT_PNL_PCT,
            "max_drawdown_pct": _EDGE_MAX_DRAWDOWN_PCT,
            "min_positive_months": _EDGE_MIN_POSITIVE_MONTHS,
            "min_stable_positive_months": _EDGE_MIN_STABLE_POSITIVE_MONTHS,
            "max_top3_trade_pnl_share": _EDGE_MAX_TOP3_TRADE_SHARE,
            "max_top3_symbol_pnl_share": _EDGE_MAX_TOP3_SYMBOL_SHARE,
            "max_top5_symbol_pnl_share": _EDGE_MAX_TOP5_SYMBOL_SHARE,
            "max_best_month_pnl_share": _EDGE_MAX_BEST_MONTH_SHARE,
        },
        "best_combo_id": best_combo_id,
        "best_combo_variant": best_summary.iloc[0]["combo_variant"],
        "best_combo_equity_metrics": best_equity_metrics,
        "best_confirmed_combo_variant": confirmed_best_summary.iloc[0]["combo_variant"] if not confirmed_best_summary.empty else None,
        "best_confirmed_combo_equity_metrics": confirmed_best_summary.iloc[0].to_dict() if not confirmed_best_summary.empty else None,
    }

    artifacts = {
        "atomic_summary": output_dir / "unified_edge_atomic_summary.csv",
        "combo_summary": output_dir / "unified_edge_combo_summary.csv",
        "best_summary": output_dir / "unified_edge_best_summary.csv",
        "best_monthly": output_dir / "unified_edge_best_monthly.csv",
        "best_monthly_stability": output_dir / "unified_edge_best_monthly_stability.csv",
        "best_holdout": output_dir / "unified_edge_best_holdout.csv",
        "best_events": output_dir / "unified_edge_best_events.csv",
        "best_risk_ladder": output_dir / "unified_edge_best_risk_ladder.csv",
        "confirmed_combo_summary": output_dir / "unified_edge_confirmed_combo_summary.csv",
        "confirmed_best_summary": output_dir / "unified_edge_best_confirmed_summary.csv",
        "confirmed_best_monthly": output_dir / "unified_edge_best_confirmed_monthly.csv",
        "confirmed_best_monthly_stability": output_dir / "unified_edge_best_confirmed_monthly_stability.csv",
        "confirmed_best_holdout": output_dir / "unified_edge_best_confirmed_holdout.csv",
        "confirmed_best_events": output_dir / "unified_edge_best_confirmed_events.csv",
        "confirmed_best_risk_ladder": output_dir / "unified_edge_best_confirmed_risk_ladder.csv",
        "behavior_by_entry": output_dir / "unified_edge_behavior_by_entry.csv",
        "behavior_by_stop": output_dir / "unified_edge_behavior_by_stop.csv",
        "behavior_by_trail": output_dir / "unified_edge_behavior_by_trail.csv",
        "behavior_by_exit": output_dir / "unified_edge_behavior_by_exit.csv",
        "context": output_dir / "unified_edge_context.json",
        "report": output_dir / "unified_edge_report.md",
        "charts_dir": charts_dir,
        **top_trade_chart_artifacts,
    }
    atomic_summary.to_csv(artifacts["atomic_summary"], index=False)
    combo_summary.to_csv(artifacts["combo_summary"], index=False)
    best_summary.to_csv(artifacts["best_summary"], index=False)
    best_monthly.to_csv(artifacts["best_monthly"], index=False)
    best_monthly_stability.to_csv(artifacts["best_monthly_stability"], index=False)
    best_holdout.to_csv(artifacts["best_holdout"], index=False)
    best_events.to_csv(artifacts["best_events"], index=False)
    best_risk_ladder.to_csv(artifacts["best_risk_ladder"], index=False)
    confirmed_combo_summary.to_csv(artifacts["confirmed_combo_summary"], index=False)
    confirmed_best_summary.to_csv(artifacts["confirmed_best_summary"], index=False)
    confirmed_best_monthly.to_csv(artifacts["confirmed_best_monthly"], index=False)
    confirmed_best_monthly_stability.to_csv(artifacts["confirmed_best_monthly_stability"], index=False)
    confirmed_best_holdout.to_csv(artifacts["confirmed_best_holdout"], index=False)
    confirmed_best_events.to_csv(artifacts["confirmed_best_events"], index=False)
    confirmed_best_risk_ladder.to_csv(artifacts["confirmed_best_risk_ladder"], index=False)
    behavior_by_entry.to_csv(artifacts["behavior_by_entry"], index=False)
    behavior_by_stop.to_csv(artifacts["behavior_by_stop"], index=False)
    behavior_by_trail.to_csv(artifacts["behavior_by_trail"], index=False)
    behavior_by_exit.to_csv(artifacts["behavior_by_exit"], index=False)
    artifacts["context"].write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    _build_report_v2(
        report_path=artifacts["report"],
        context=context,
        atomic_summary=atomic_summary.head(12),
        best_summary=best_summary,
        best_monthly=best_monthly_stability,
        best_holdout=best_holdout,
        best_risk_ladder=best_risk_ladder,
        confirmed_best_summary=confirmed_best_summary,
        confirmed_best_monthly=confirmed_best_monthly_stability,
        confirmed_best_holdout=confirmed_best_holdout,
        confirmed_best_risk_ladder=confirmed_best_risk_ladder,
        behavior_by_entry=behavior_by_entry,
        behavior_by_stop=behavior_by_stop,
        behavior_by_trail=behavior_by_trail,
        behavior_by_exit=behavior_by_exit,
    )
    best_goal = bool(best_summary.iloc[0]["meets_goal"]) if not best_summary.empty else False
    best_distribution_goal = bool(best_summary.iloc[0]["meets_distribution_goal"]) if not best_summary.empty else False
    active_logger.info("unified-edge: stage=done atomic=%s combos=%s best_variant=%s goal=%s distribution_goal=%s", len(atomic_summary), len(combo_summary), best_summary.iloc[0]["combo_variant"] if not best_summary.empty else "n/a", best_goal, best_distribution_goal)
    return {**artifacts, "goal_passed": best_goal, "distribution_goal_passed": best_distribution_goal}


def build_hourly_asia_pump_unified_edge_top_trade_charts(
    *,
    best_events_path: Path,
    output_dir: Path,
    cache_dir: Path | str,
    logger: logging.Logger | None = None,
    top_n: int = 10,
) -> dict[str, Path]:
    active_logger = logger or module_logger
    events = pd.read_csv(best_events_path)
    if events.empty:
        raise ValueError(f"Не найдены сделки в {best_events_path}")
    charts_dir = output_dir / "unified_edge_charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    preparer = DataPreparer(Path(cache_dir))
    artifacts = _build_top_trade_charts(
        events=events,
        preparer=preparer,
        charts_dir=charts_dir,
        logger=active_logger,
        top_n=top_n,
    )
    active_logger.info(
        "unified-edge: stage=trade-charts-done winners_dir=%s losers_dir=%s",
        artifacts["top_winners_dir"],
        artifacts["top_losers_dir"],
    )
    return artifacts
