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
from vectorbt_runner.data_preparer import DataPreparer

module_logger = logging.getLogger(__name__)

_EDGE_MIN_TRADES_PER_YEAR = 50.0
_EDGE_MIN_MEAN_RETURN_PCT = 0.025
_EDGE_MIN_WIN_RATE = 0.40
_EDGE_MIN_ANNUALIZED_UNIT_PNL_PCT = 1.0
_EDGE_MAX_DRAWDOWN_PCT = 0.30
_EDGE_MIN_POSITIVE_MONTHS = 9
_EDGE_MAX_TOP3_TRADE_SHARE = 0.70
_EDGE_MAX_TOP5_TRADE_SHARE = 0.85
_EDGE_MAX_BEST_MONTH_SHARE = 0.55
_EDGE_COMBO_CANDIDATE_LIMIT = 40
_EDGE_MAX_COMBO_COMPONENTS = 3
_EDGE_PROGRESS_LOG_EVERY_SYMBOLS = 25
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
        {"entry_profile_id": "next_open", "entry_style": "next_bar_open", "max_entry_bars": 1, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_25g", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.25, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_25g_strong", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.25, "min_next_bar_low_frac_of_trigger_range": 0.50, "min_next_bar_return_pct": 0.0, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_33", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": 0.33, "min_next_bar_low_frac_of_trigger_range": 0.35, "min_next_bar_return_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "confirm_50g", "entry_style": "confirmed_next_open", "max_entry_bars": 0, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": True, "max_next_bar_pullback_frac": 0.50, "min_next_bar_low_frac_of_trigger_range": 0.25, "min_next_bar_return_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "break_1", "entry_style": "break_trigger_high", "max_entry_bars": 1, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "break_2", "entry_style": "break_trigger_high", "max_entry_bars": 2, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "max_pre_entry_pullback_frac": None, "max_pre_entry_red_volume_frac": None, "min_pre_entry_low_frac_of_trigger_range": None},
        {"entry_profile_id": "break_2_hold", "entry_style": "break_trigger_high", "max_entry_bars": 2, "max_pullback_frac": 0.0, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "max_pre_entry_pullback_frac": 0.25, "max_pre_entry_red_volume_frac": 0.80, "min_pre_entry_low_frac_of_trigger_range": 0.50},
        {"entry_profile_id": "pullback_25", "entry_style": "pullback_reclaim", "max_entry_bars": 3, "max_pullback_frac": 0.25, "pullback_volume_frac": 1.0, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "max_pre_entry_pullback_frac": 0.35, "max_pre_entry_red_volume_frac": 1.0, "min_pre_entry_low_frac_of_trigger_range": 0.35},
        {"entry_profile_id": "pressure_25", "entry_style": "pressure_reclaim", "max_entry_bars": 3, "max_pullback_frac": 0.25, "pullback_volume_frac": 0.80, "flag_bars": 0, "flag_max_range_frac": 0.0, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "max_pre_entry_pullback_frac": 0.35, "max_pre_entry_red_volume_frac": 0.80, "min_pre_entry_low_frac_of_trigger_range": 0.35},
        {"entry_profile_id": "flag_2", "entry_style": "flag_break", "max_entry_bars": 2, "max_pullback_frac": 0.35, "pullback_volume_frac": 1.0, "flag_bars": 2, "flag_max_range_frac": 0.60, "require_next_bar_green": False, "max_next_bar_pullback_frac": None, "min_next_bar_low_frac_of_trigger_range": None, "min_next_bar_return_pct": None, "max_pre_entry_pullback_frac": 0.35, "max_pre_entry_red_volume_frac": 1.0, "min_pre_entry_low_frac_of_trigger_range": 0.35},
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
    for signal_profile in _build_signal_profiles():
        for entry_profile in _build_entry_profiles():
            for exit_profile in _build_exit_profiles():
                if entry_profile["entry_style"] == "next_bar_open" and exit_profile["initial_stop_style"] == "pattern_low":
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
    )


def _candidate_meets_distribution_goal(row: pd.Series | dict[str, object]) -> bool:
    top3_share = _safe_float(row.get("top3_trade_pnl_share"))
    top5_share = _safe_float(row.get("top5_trade_pnl_share"))
    best_month_share = _safe_float(row.get("best_month_pnl_share"))
    annualized_remove_top3 = float(row.get("annualized_remove_top3_trade_pct", 0.0) or 0.0)
    annualized_remove_top5 = float(row.get("annualized_remove_top5_trade_pct", 0.0) or 0.0)
    annualized_remove_best_month = float(row.get("annualized_remove_best_month_pct", 0.0) or 0.0)
    return (
        top3_share is not None
        and top5_share is not None
        and best_month_share is not None
        and top3_share <= _EDGE_MAX_TOP3_TRADE_SHARE
        and top5_share <= _EDGE_MAX_TOP5_TRADE_SHARE
        and best_month_share <= _EDGE_MAX_BEST_MONTH_SHARE
        and annualized_remove_top3 > 0.20
        and annualized_remove_top5 > 0.10
        and annualized_remove_best_month > 0.20
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
    top1_share = float(_safe_float(row.get("top1_trade_pnl_share")) or 0.0)
    top3_share = float(_safe_float(row.get("top3_trade_pnl_share")) or 0.0)
    top5_share = float(_safe_float(row.get("top5_trade_pnl_share")) or 0.0)
    best_month_share = float(_safe_float(row.get("best_month_pnl_share")) or 0.0)
    components_count = int(row.get("components_count", 1) or 1)

    score = 0.0
    score += min(mean_return_pct, 0.06) * 1500.0
    score += min(max(median_return_pct, -0.02), 0.04) * 500.0
    score += min(win_rate, 0.70) * 250.0
    score += min(annualized, 3.5) * 55.0
    score += min(trades_per_year, 150.0) * 0.20
    score += min(positive_months, 12) * 10.0
    score += max(annualized_remove_top1, -1.0) * 15.0
    score += max(annualized_remove_top3, -1.0) * 35.0
    score += max(annualized_remove_top5, -1.0) * 20.0
    score += max(annualized_remove_best_month, -1.0) * 35.0
    score -= max(max_drawdown - 0.20, 0.0) * 220.0
    score -= top1_share * 20.0
    score -= top3_share * 45.0
    score -= top5_share * 20.0
    score -= best_month_share * 45.0
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
                "component_rule_texts": " || ".join(component_slice["rule_text"].astype(str).tolist()),
                **summary,
                **_build_concentration_metrics(combo_events, calendar_months=calendar_months),
            }
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
        rows.append({"split_id": split_id, "train_months": train_months, "test_months": len(test_months), "test_month_list": ",".join(test_months), **summary, **_build_concentration_metrics(scoped, calendar_months=test_months)})
    return pd.DataFrame(rows)


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
    best_summary = combo_summary.head(1).copy()
    best_combo_id = str(best_summary.iloc[0]["combo_id"])
    best_events = combo_events_by_id[best_combo_id].copy().reset_index(drop=True)
    best_monthly = _build_monthly_returns_frame(best_events, calendar_months=calendar_months)
    best_holdout = _build_holdout_summary(events=best_events, calendar_months=calendar_months)

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

    context = {
        "search_scope": {
            "uses_hour_utc_in_optimization": False,
            "same_rules_for_all_xx00": True,
            "timeframe": timeframe.value,
            "events_count": int(len(selected_events)),
            "atomic_models_count": int(len(models)),
            "commission_rate": float(commission_rate),
            "round_trip_taker_fee_pct": float(commission_rate * 2.0),
        },
        "criteria": {
            "min_trades_per_year": _EDGE_MIN_TRADES_PER_YEAR,
            "min_mean_return_pct": _EDGE_MIN_MEAN_RETURN_PCT,
            "min_win_rate": _EDGE_MIN_WIN_RATE,
            "min_annualized_unit_pnl_pct": _EDGE_MIN_ANNUALIZED_UNIT_PNL_PCT,
            "max_drawdown_pct": _EDGE_MAX_DRAWDOWN_PCT,
            "min_positive_months": _EDGE_MIN_POSITIVE_MONTHS,
            "max_top3_trade_pnl_share": _EDGE_MAX_TOP3_TRADE_SHARE,
            "max_best_month_pnl_share": _EDGE_MAX_BEST_MONTH_SHARE,
        },
        "best_combo_id": best_combo_id,
        "best_combo_variant": best_summary.iloc[0]["combo_variant"],
    }

    artifacts = {
        "atomic_summary": output_dir / "unified_edge_atomic_summary.csv",
        "combo_summary": output_dir / "unified_edge_combo_summary.csv",
        "best_summary": output_dir / "unified_edge_best_summary.csv",
        "best_monthly": output_dir / "unified_edge_best_monthly.csv",
        "best_holdout": output_dir / "unified_edge_best_holdout.csv",
        "best_events": output_dir / "unified_edge_best_events.csv",
        "behavior_by_entry": output_dir / "unified_edge_behavior_by_entry.csv",
        "behavior_by_stop": output_dir / "unified_edge_behavior_by_stop.csv",
        "behavior_by_trail": output_dir / "unified_edge_behavior_by_trail.csv",
        "behavior_by_exit": output_dir / "unified_edge_behavior_by_exit.csv",
        "context": output_dir / "unified_edge_context.json",
        "report": output_dir / "unified_edge_report.md",
        "charts_dir": charts_dir,
    }
    atomic_summary.to_csv(artifacts["atomic_summary"], index=False)
    combo_summary.to_csv(artifacts["combo_summary"], index=False)
    best_summary.to_csv(artifacts["best_summary"], index=False)
    best_monthly.to_csv(artifacts["best_monthly"], index=False)
    best_holdout.to_csv(artifacts["best_holdout"], index=False)
    best_events.to_csv(artifacts["best_events"], index=False)
    behavior_by_entry.to_csv(artifacts["behavior_by_entry"], index=False)
    behavior_by_stop.to_csv(artifacts["behavior_by_stop"], index=False)
    behavior_by_trail.to_csv(artifacts["behavior_by_trail"], index=False)
    behavior_by_exit.to_csv(artifacts["behavior_by_exit"], index=False)
    artifacts["context"].write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    _build_report(
        report_path=artifacts["report"],
        context=context,
        atomic_summary=atomic_summary.head(12),
        best_summary=best_summary,
        best_monthly=best_monthly,
        best_holdout=best_holdout,
        behavior_by_entry=behavior_by_entry,
        behavior_by_stop=behavior_by_stop,
        behavior_by_trail=behavior_by_trail,
        behavior_by_exit=behavior_by_exit,
    )
    best_goal = bool(best_summary.iloc[0]["meets_goal"]) if not best_summary.empty else False
    best_distribution_goal = bool(best_summary.iloc[0]["meets_distribution_goal"]) if not best_summary.empty else False
    active_logger.info("unified-edge: stage=done atomic=%s combos=%s best_variant=%s goal=%s distribution_goal=%s", len(atomic_summary), len(combo_summary), best_summary.iloc[0]["combo_variant"] if not best_summary.empty else "n/a", best_goal, best_distribution_goal)
    return {**artifacts, "goal_passed": best_goal, "distribution_goal_passed": best_distribution_goal}
