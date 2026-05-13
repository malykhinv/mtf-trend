"""Research backtest for early anomaly-continuation long entries."""

from __future__ import annotations

import argparse
import math
import re
import time
from dataclasses import asdict
from dataclasses import replace
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import numpy as np
import pandas as pd

from research_tools.charting import (
    CHART_ANOMALY,
    CHART_DOWN,
    CHART_EMA9,
    CHART_EMA20,
    CHART_ENTRY,
    CHART_EXIT,
    CHART_FIGURE_FACE,
    CHART_LEVEL,
    CHART_MUTED,
    CHART_PROFIT_EDGE,
    CHART_PROFIT_FACE,
    CHART_RISK_EDGE,
    CHART_RISK_FACE,
    CHART_SAVEFIG_KWARGS,
    CHART_TEXT,
    CHART_UP,
    TRADE_CHART_FIGSIZE,
    annotate_axis_price_tag,
    build_tick_labels_from_timestamps,
    build_tick_positions_from_timestamps,
    build_tick_timestamps,
    configure_plot_axes,
    draw_candles,
    draw_price_zone,
    format_chart_symbol,
    infer_frame_step_ms,
    resolve_axis_tag_positions,
    resolve_candle_width,
    resolve_timestamp_plot_idx,
)
from research_tools.anomaly_continuation_lab import (
    AnomalyLabConfig,
    DERIVATIVES_CONTEXT_SPECS,
    build_derivatives_context_status,
    build_oi_context_status,
    collect_anomaly_lab_rows,
    compute_start_verticality_metrics,
    enrich_candidates_with_open_interest,
    enrich_candidates_with_derivatives_context,
    _empty_context_columns,
    _emit_progress,
)

_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_TRADE_CHART_CONTEXT_DAYS = 4
_MATERIALIZED_SUBMINUTE_CACHE_VERSION = "p165_1s_ohlcv_to_subminute_v1"
_BAD_CONTEXT_STATUSES = {"error", "missing_columns", "missing_frame", "stale_asof"}
_TRADE_CHART_FLOW_PROVENANCE = {
    "trade_count_proxy_used": False,
    "levels_trade_count_source": "cached_ohlcv.number_of_trades",
    "entry_trade_count_source": "cached_ohlcv.number_of_trades",
    "levels_quote_volume_source": "cached_ohlcv.quote_volume",
    "entry_quote_volume_source": "cached_ohlcv.quote_volume",
}

TRADE_SIGNAL_CONTEXT_COLUMNS = (
    "trade_count_proxy_used",
    "levels_trade_count_source",
    "entry_trade_count_source",
    "levels_quote_volume_source",
    "entry_quote_volume_source",
    "feature_contract",
    "setup_timeframe",
    "entry_timeframe",
    "setup_source",
    "setup_elapsed_fraction",
    "setup_closed_entry_candles",
    "entry_activation_price",
    "start_trade_count",
    "baseline_trade_count_median",
    "baseline_quote_volume_median",
    "start_trade_ratio",
    "start_quote_ratio",
    "start_quote_ratio_raw",
    "start_trade_ratio_raw",
    "start_quote_pace_ratio",
    "start_trade_pace_ratio",
    "next_n_trade_count_mean",
    "next_n_quote_volume_mean",
    "hold_count_next_n_candles",
    "hold_ratio_next_n_candles",
    "hold_count_model",
    "flow_hold_count_next_n_candles",
    "flow_hold_ratio_next_n_candles",
    "price_retention_model",
    "next_n_trade_decay",
    "next_n_quote_decay",
    "price_retention_next_n",
    "new_high_count_next_n",
    "start_verticality_score",
    "start_verticality_path_efficiency",
    "start_verticality_range_efficiency",
    "start_verticality_slope_pct_per_candle",
    "start_verticality_max_retrace_fraction",
    "start_verticality_green_share",
    "flow_taker_buy_status",
    "start_taker_buy_quote_share",
    "baseline_taker_buy_quote_share_median",
    "start_taker_buy_quote_share_delta",
    "next_n_taker_buy_quote_share_mean",
    "next_n_taker_buy_quote_share_delta",
    "next_n_taker_buy_quote_share_decay",
    "start_avg_trade_quote_size",
    "baseline_avg_trade_quote_size_median",
    "start_avg_trade_quote_size_ratio",
    "next_n_avg_trade_quote_size_mean",
    "next_n_avg_trade_quote_size_decay",
    "decision_return_from_start_open",
    "start_close_position_in_range",
    "start_body_to_range",
    "start_upper_wick_to_range",
    "start_lower_wick_to_range",
    "baseline_range_median",
    "baseline_range_pct_median",
    "start_range_ratio_to_baseline",
    "start_range_pct",
    "start_range_pct_ratio_to_baseline",
    "baseline_zero_range_share",
    "baseline_return_range_pct",
    "baseline_close_return_range_pct",
    "baseline_return_from_first_close_pct",
    "prior_up_leg_to_impulse_range",
    "prior_down_leg_to_impulse_range",
    "prior_up_down_whipsaw_to_impulse_range",
    "start_quote_per_abs_return",
    "start_trades_per_abs_return",
    "start_quote_ratio_per_abs_return",
    "start_trade_ratio_per_abs_return",
    "prior_spike_count_24h",
    "prior_spike_count_72h",
    "prior_fast_fade_count_24h",
    "prior_fast_fade_count_72h",
    "prior_big_move_count_24h",
    "prior_big_move_count_72h",
    "prior_spike_density_72h",
    "time_since_prior_spike_ms",
    "time_since_prior_spike_hours",
    "post_start_pullback_fraction_of_box",
    "oi_timeframe",
    "oi_status",
    "oi_timestamp_ms",
    "oi_timestamp_utc",
    "oi_age_ms",
    "oi_open_interest",
    "oi_change_1x5m",
    "oi_change_pct_1x5m",
    "oi_change_3x5m",
    "oi_change_pct_3x5m",
    "oi_change_6x5m",
    "oi_change_pct_6x5m",
    "oi_price_interaction_3x5m",
    "oi_change_pct_3x5m_per_decision_return",
    "mark_close_vs_decision_close_basis",
    "taker_ls_buy_share",
)

for _context_spec in DERIVATIVES_CONTEXT_SPECS:
    _context_prefix = str(_context_spec["prefix"])
    TRADE_SIGNAL_CONTEXT_COLUMNS += (
        f"{_context_prefix}_status",
        f"{_context_prefix}_timestamp_ms",
        f"{_context_prefix}_timestamp_utc",
        f"{_context_prefix}_age_ms",
    )
    for _context_column in _context_spec["value_columns"]:
        _context_column_name = str(_context_column)
        TRADE_SIGNAL_CONTEXT_COLUMNS += (f"{_context_prefix}_{_context_column_name}",)
        for _context_bars in _context_spec["lookback_bars"]:
            TRADE_SIGNAL_CONTEXT_COLUMNS += (
                f"{_context_prefix}_{_context_column_name}_change_{int(_context_bars)}",
                f"{_context_prefix}_{_context_column_name}_change_pct_{int(_context_bars)}",
            )


@dataclass(frozen=True, slots=True)
class AnomalyBacktestConfig:
    lab_config: AnomalyLabConfig
    setup_timeframe: str | None = None
    entry_timeframe: str | None = None
    feature_contract: str = "closed_setup_tf_v1"
    min_price_retention: float = 0.70
    max_price_retention: float | None = None
    min_verticality_score: float = 0.25
    min_hold_count: int = 2
    min_oi_change_pct_3x5m: float | None = None
    require_oi_status_ok: bool = False
    exhaustion_profile: str = "none"
    max_start_quote_ratio: float | None = None
    max_start_trade_ratio: float | None = None
    max_start_avg_trade_quote_size_ratio: float | None = None
    max_start_quote_ratio_per_abs_return: float | None = None
    max_start_range_pct_ratio_to_baseline: float | None = None
    max_prior_up_down_whipsaw_to_impulse_range: float | None = 0.60
    min_flow_hold_count: int | None = None
    max_prior_spike_count_72h: int | None = None
    max_prior_fast_fade_count_72h: int | None = None
    min_start_lower_wick_to_range: float | None = None
    max_start_upper_wick_to_range: float | None = None
    min_next_taker_buy_quote_share: float | None = None
    red_flag_profile: str = "none"
    min_mark_close_vs_decision_close_basis: float | None = None
    reject_oi_down_mark_discount: bool = False
    reject_stale_derivatives_context: bool = False
    max_start_taker_buy_quote_share_delta: float | None = None
    max_next_taker_buy_quote_share_delta: float | None = None
    max_start_trade_ratio_per_abs_return: float | None = None
    max_initial_risk_pct: float = 0.16
    entry_method: str = "market"
    pullback_box_fraction: float = 0.75
    entry_timeout_candles: int = 60
    market_entry_latency_candles: int = 1
    max_market_entry_drift_pct: float = 0.003
    min_market_rr_to_signal_tp1: float = 0.75
    stop_buffer_range_fraction: float = 0.05
    tp1_r: float = 1.0
    tp1_fraction: float = 0.50
    move_stop_to_breakeven_after_tp1: bool = True
    trail_lookback_candles: int = 5
    trail_buffer_r: float = 0.10
    exit_rule: str = "structural_trail"
    max_hold_candles: int = 240
    fee_rate: float = 0.0004


EXECUTION_GUARD_SKIP_REASONS = {
    "no_market_execution_candle",
    "invalid_market_execution_price",
    "tp1_already_reached_before_market_entry",
    "invalid_actual_market_risk",
    "market_entry_price_drift",
    "market_entry_rr_collapsed",
}


def _execution_model_label(config: AnomalyBacktestConfig) -> str:
    if config.entry_method == "market":
        return f"next_bar_open_proxy_latency_{config.market_entry_latency_candles}"
    return config.entry_method


def _apply_red_flag_profile(config: AnomalyBacktestConfig) -> AnomalyBacktestConfig:
    profile = str(config.red_flag_profile or "none").strip().lower()
    if profile == "none":
        return config
    if profile == "cautious":
        return replace(
            config,
            min_mark_close_vs_decision_close_basis=(
                0.0
                if config.min_mark_close_vs_decision_close_basis is None
                else config.min_mark_close_vs_decision_close_basis
            ),
            reject_oi_down_mark_discount=True if not config.reject_oi_down_mark_discount else config.reject_oi_down_mark_discount,
            reject_stale_derivatives_context=True
            if not config.reject_stale_derivatives_context
            else config.reject_stale_derivatives_context,
            max_start_taker_buy_quote_share_delta=(
                0.50
                if config.max_start_taker_buy_quote_share_delta is None
                else config.max_start_taker_buy_quote_share_delta
            ),
            max_next_taker_buy_quote_share_delta=(
                0.40
                if config.max_next_taker_buy_quote_share_delta is None
                else config.max_next_taker_buy_quote_share_delta
            ),
            max_start_quote_ratio_per_abs_return=(
                50_000.0
                if config.max_start_quote_ratio_per_abs_return is None
                else config.max_start_quote_ratio_per_abs_return
            ),
            max_start_trade_ratio_per_abs_return=(
                5_000.0
                if config.max_start_trade_ratio_per_abs_return is None
                else config.max_start_trade_ratio_per_abs_return
            ),
        )
    if profile == "strict":
        return replace(
            config,
            min_mark_close_vs_decision_close_basis=(
                0.001
                if config.min_mark_close_vs_decision_close_basis is None
                else config.min_mark_close_vs_decision_close_basis
            ),
            reject_oi_down_mark_discount=True if not config.reject_oi_down_mark_discount else config.reject_oi_down_mark_discount,
            reject_stale_derivatives_context=True
            if not config.reject_stale_derivatives_context
            else config.reject_stale_derivatives_context,
            max_start_taker_buy_quote_share_delta=(
                0.30
                if config.max_start_taker_buy_quote_share_delta is None
                else config.max_start_taker_buy_quote_share_delta
            ),
            max_next_taker_buy_quote_share_delta=(
                0.30
                if config.max_next_taker_buy_quote_share_delta is None
                else config.max_next_taker_buy_quote_share_delta
            ),
            max_start_quote_ratio_per_abs_return=(
                25_000.0
                if config.max_start_quote_ratio_per_abs_return is None
                else config.max_start_quote_ratio_per_abs_return
            ),
            max_start_trade_ratio_per_abs_return=(
                5_000.0
                if config.max_start_trade_ratio_per_abs_return is None
                else config.max_start_trade_ratio_per_abs_return
            ),
        )
    if profile == "runner_balanced":
        return replace(
            config,
            min_mark_close_vs_decision_close_basis=(
                0.001
                if config.min_mark_close_vs_decision_close_basis is None
                else config.min_mark_close_vs_decision_close_basis
            ),
            max_start_quote_ratio=(
                1000.0 if config.max_start_quote_ratio is None else config.max_start_quote_ratio
            ),
            max_start_trade_ratio=(
                250.0 if config.max_start_trade_ratio is None else config.max_start_trade_ratio
            ),
            max_start_quote_ratio_per_abs_return=(
                20_000.0
                if config.max_start_quote_ratio_per_abs_return is None
                else config.max_start_quote_ratio_per_abs_return
            ),
            max_start_trade_ratio_per_abs_return=(
                3_000.0
                if config.max_start_trade_ratio_per_abs_return is None
                else config.max_start_trade_ratio_per_abs_return
            ),
            max_start_taker_buy_quote_share_delta=(
                0.25
                if config.max_start_taker_buy_quote_share_delta is None
                else config.max_start_taker_buy_quote_share_delta
            ),
            max_prior_fast_fade_count_72h=(
                0
                if config.max_prior_fast_fade_count_72h is None
                else config.max_prior_fast_fade_count_72h
            ),
        )
    if profile == "runner_reclaim":
        balanced = _apply_red_flag_profile(replace(config, red_flag_profile="runner_balanced"))
        return replace(
            balanced,
            red_flag_profile=config.red_flag_profile,
            min_start_lower_wick_to_range=(
                0.0
                if balanced.min_start_lower_wick_to_range is None
                else balanced.min_start_lower_wick_to_range
            ),
            max_start_upper_wick_to_range=(
                0.20
                if balanced.max_start_upper_wick_to_range is None
                else balanced.max_start_upper_wick_to_range
            ),
        )
    if profile == "runner_flow":
        balanced = _apply_red_flag_profile(replace(config, red_flag_profile="runner_balanced"))
        return replace(
            balanced,
            red_flag_profile=config.red_flag_profile,
            min_flow_hold_count=(
                1
                if balanced.min_flow_hold_count is None
                else balanced.min_flow_hold_count
            ),
        )
    raise ValueError(f"unsupported red_flag_profile: {config.red_flag_profile}")


def _context_status_columns(frame: pd.DataFrame) -> list[str]:
    return [
        column
        for column in frame.columns
        if column == "oi_status" or column.endswith("_status")
    ]


def _red_flag_violation_masks(signals: pd.DataFrame, *, config: AnomalyBacktestConfig) -> dict[str, pd.Series]:
    if signals.empty:
        return {}
    masks: dict[str, pd.Series] = {}
    index = signals.index
    if config.min_mark_close_vs_decision_close_basis is not None:
        mark_basis = pd.to_numeric(signals["mark_close_vs_decision_close_basis"], errors="coerce")
        masks["mark_basis_below_min"] = mark_basis.lt(config.min_mark_close_vs_decision_close_basis) | mark_basis.isna()
    if config.reject_oi_down_mark_discount:
        mark_basis = pd.to_numeric(signals["mark_close_vs_decision_close_basis"], errors="coerce")
        masks["oi_down_with_mark_discount"] = (
            signals["oi_price_interaction_3x5m"].astype(str).eq("oi_down_price_up")
            & mark_basis.le(0.0)
        ) | mark_basis.isna()
    if config.reject_stale_derivatives_context:
        status_columns = _context_status_columns(signals)
        if status_columns:
            bad_status = pd.Series(False, index=index)
            for column in status_columns:
                bad_status |= signals[column].astype(str).isin(_BAD_CONTEXT_STATUSES)
            masks["stale_or_missing_market_context"] = bad_status
        else:
            masks["stale_or_missing_market_context"] = pd.Series(True, index=index)
    if config.max_start_taker_buy_quote_share_delta is not None:
        start_delta = pd.to_numeric(signals["start_taker_buy_quote_share_delta"], errors="coerce")
        masks["start_taker_buy_delta_above_max"] = (
            start_delta.gt(config.max_start_taker_buy_quote_share_delta) | start_delta.isna()
        )
    if config.max_next_taker_buy_quote_share_delta is not None:
        next_delta = pd.to_numeric(signals["next_n_taker_buy_quote_share_delta"], errors="coerce")
        masks["confirmation_taker_buy_delta_above_max"] = (
            next_delta.gt(config.max_next_taker_buy_quote_share_delta) | next_delta.isna()
        )
    if config.max_start_trade_ratio_per_abs_return is not None:
        trade_effort = pd.to_numeric(signals["start_trade_ratio_per_abs_return"], errors="coerce")
        masks["trade_effort_per_return_above_max"] = (
            trade_effort.gt(config.max_start_trade_ratio_per_abs_return) | trade_effort.isna()
        )
    if config.max_start_quote_ratio_per_abs_return is not None:
        quote_effort = pd.to_numeric(signals["start_quote_ratio_per_abs_return"], errors="coerce")
        masks["quote_effort_per_return_above_max"] = (
            quote_effort.gt(config.max_start_quote_ratio_per_abs_return) | quote_effort.isna()
        )
    if config.max_start_quote_ratio is not None:
        quote_ratio = pd.to_numeric(signals["start_quote_ratio"], errors="coerce")
        masks["start_quote_ratio_above_max"] = quote_ratio.gt(config.max_start_quote_ratio) | quote_ratio.isna()
    if config.max_start_trade_ratio is not None:
        trade_ratio = pd.to_numeric(signals["start_trade_ratio"], errors="coerce")
        masks["start_trade_ratio_above_max"] = trade_ratio.gt(config.max_start_trade_ratio) | trade_ratio.isna()
    if config.min_flow_hold_count is not None:
        flow_hold = pd.to_numeric(signals["flow_hold_count_next_n_candles"], errors="coerce")
        masks["flow_hold_count_below_min"] = flow_hold.lt(config.min_flow_hold_count) | flow_hold.isna()
    if config.max_prior_spike_count_72h is not None:
        prior_spikes = pd.to_numeric(signals["prior_spike_count_72h"], errors="coerce").fillna(0.0)
        masks["prior_spike_count_72h_above_max"] = prior_spikes.gt(config.max_prior_spike_count_72h)
    if config.max_prior_fast_fade_count_72h is not None:
        prior_fast_fades = pd.to_numeric(signals["prior_fast_fade_count_72h"], errors="coerce").fillna(0.0)
        masks["prior_fast_fade_count_72h_above_max"] = prior_fast_fades.gt(config.max_prior_fast_fade_count_72h)
    if config.min_start_lower_wick_to_range is not None:
        lower_wick = pd.to_numeric(signals["start_lower_wick_to_range"], errors="coerce")
        masks["start_lower_wick_below_min"] = lower_wick.le(config.min_start_lower_wick_to_range) | lower_wick.isna()
    if config.max_start_upper_wick_to_range is not None:
        upper_wick = pd.to_numeric(signals["start_upper_wick_to_range"], errors="coerce")
        masks["start_upper_wick_above_max"] = upper_wick.gt(config.max_start_upper_wick_to_range) | upper_wick.isna()
    return masks


def build_red_flag_summary(candidates: pd.DataFrame, *, config: AnomalyBacktestConfig) -> pd.DataFrame:
    config = _apply_red_flag_profile(config)
    if candidates.empty:
        return pd.DataFrame(
            [{"red_flag": "__total_candidates__", "rows": 0, "share": 0.0, "profile": config.red_flag_profile}]
        )
    masks = _red_flag_violation_masks(candidates, config=config)
    rows: list[dict[str, object]] = [
        {
            "red_flag": "__total_candidates__",
            "rows": int(len(candidates)),
            "share": 1.0,
            "profile": config.red_flag_profile,
        }
    ]
    for name, mask in masks.items():
        count = int(mask.fillna(False).sum())
        rows.append(
            {
                "red_flag": name,
                "rows": count,
                "share": _safe_divide_value(count, len(candidates)),
                "profile": config.red_flag_profile,
            }
        )
    if masks:
        any_mask = pd.Series(False, index=candidates.index)
        for mask in masks.values():
            any_mask |= mask.fillna(False)
        count = int(any_mask.sum())
        rows.append(
            {
                "red_flag": "__any_red_flag__",
                "rows": count,
                "share": _safe_divide_value(count, len(candidates)),
                "profile": config.red_flag_profile,
            }
        )
    return pd.DataFrame(rows)


def _emit_progress_5pct(
    *,
    label: str,
    done: int,
    total: int,
    started_at: float,
    next_progress_pct: int,
) -> int:
    if total <= 0:
        return next_progress_pct
    current_pct = int(100 * done / total)
    if current_pct >= next_progress_pct or done == total:
        _emit_progress(label=label, done=done, total=total, started_at=started_at)
        return current_pct + 5
    return next_progress_pct


def _write_artifact_frames(
    frames: Iterable[tuple[Path, pd.DataFrame]],
    *,
    progress_label: str,
) -> None:
    frame_list = list(frames)
    started_at = time.monotonic()
    next_progress_pct = 0
    for processed_count, (path, frame) in enumerate(frame_list, start=1):
        frame.to_csv(path, index=False)
        next_progress_pct = _emit_progress_5pct(
            label=progress_label,
            done=processed_count,
            total=len(frame_list),
            started_at=started_at,
            next_progress_pct=next_progress_pct,
        )


def _sanitize_file_part(value: object) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value))
    return cleaned.strip("._") or "item"


def _timestamp_to_utc(timestamp_ms: int | float) -> str:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC).isoformat()


def _safe_float(value: object) -> float | None:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(resolved):
        return None
    return resolved


def _safe_int(value: object) -> int | None:
    try:
        if pd.isna(value):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _timeframe_to_milliseconds(timeframe: str) -> int:
    match = re.fullmatch(r"(\d+)([smhdw])", str(timeframe))
    if match is None:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    amount = int(match.group(1))
    unit = match.group(2)
    seconds_by_unit = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return amount * seconds_by_unit[unit] * 1000


def _effective_setup_timeframe(config: AnomalyBacktestConfig) -> str:
    return str(config.setup_timeframe or config.lab_config.timeframe)


def _effective_entry_timeframe(config: AnomalyBacktestConfig) -> str:
    return str(config.entry_timeframe or config.lab_config.timeframe)


def _symbol_from_cache_symbol_dir(symbol_dir: Path) -> str:
    from urllib.parse import unquote

    return unquote(symbol_dir.name)


def _safe_divide_value(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or abs(denominator) <= 1e-12:
        return float("nan")
    return float(numerator / denominator)


def _nice_market_round_step(*, reference_price: float, movement: float) -> float:
    if not np.isfinite(reference_price) or reference_price <= 0.0:
        return float("nan")
    raw_step = max(abs(float(movement)) * 0.25, abs(float(reference_price)) * 0.0002, 1e-12)
    exponent = math.floor(math.log10(raw_step))
    base = 10.0 ** exponent
    normalized = raw_step / base
    for multiplier in (1.0, 2.0, 5.0, 10.0):
        if normalized <= multiplier:
            return multiplier * base
    return 10.0 * base


def _round_up_tp1_to_market_number(base_tp1_price: float, *, reference_price: float, movement: float) -> tuple[float, float]:
    if not np.isfinite(base_tp1_price) or base_tp1_price <= 0.0:
        return base_tp1_price, float("nan")
    step = _nice_market_round_step(reference_price=reference_price, movement=movement)
    if not np.isfinite(step) or step <= 0.0:
        return base_tp1_price, float("nan")
    rounded = math.ceil((base_tp1_price - step * 1e-9) / step) * step
    tolerance = max(abs(float(base_tp1_price)) * 1e-12, step * 1e-9)
    if rounded <= base_tp1_price + tolerance:
        rounded += step
    decimals = max(0, int(math.ceil(-math.log10(step))) + 2) if step < 1.0 else 8
    return round(float(rounded), min(decimals, 12)), float(step)

def _prior_up_down_whipsaw_to_impulse_range(baseline: pd.DataFrame, *, impulse_range: float) -> float:
    if baseline.empty or not np.isfinite(impulse_range) or impulse_range <= 0.0:
        return float("nan")
    highs = baseline["high"].astype(float)
    lows = baseline["low"].astype(float)
    high_pos = int(highs.to_numpy().argmax()) if not highs.empty else -1
    if high_pos < 0:
        return float("nan")
    high_value = float(highs.iloc[high_pos])
    low_before_high = float(lows.iloc[: high_pos + 1].min())
    low_after_high = float(lows.iloc[high_pos:].min())
    prior_up_leg = high_value - low_before_high
    prior_down_leg = high_value - low_after_high
    up_ratio = _safe_divide_value(prior_up_leg, impulse_range)
    down_ratio = _safe_divide_value(prior_down_leg, impulse_range)
    if not np.isfinite(up_ratio) or not np.isfinite(down_ratio):
        return float("nan")
    return float(min(up_ratio, down_ratio))


def _aggregate_ohlcv_to_candle(frame: pd.DataFrame, *, timestamp_ms: int) -> pd.Series | None:
    if frame.empty:
        return None
    ordered = frame.copy().sort_values("timestamp")
    row: dict[str, object] = {
        "timestamp": int(timestamp_ms),
        "open": float(ordered.iloc[0]["open"]),
        "high": float(pd.to_numeric(ordered["high"], errors="coerce").max()),
        "low": float(pd.to_numeric(ordered["low"], errors="coerce").min()),
        "close": float(ordered.iloc[-1]["close"]),
    }
    for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in ordered.columns:
            row[column] = float(pd.to_numeric(ordered[column], errors="coerce").fillna(0.0).sum())
    return pd.Series(row)


def _cache_symbol_dir_name(symbol: str) -> str:
    return quote(symbol, safe="")


def _read_symbol_frame(cache_dir: Path, symbol: str, timeframe: str) -> pd.DataFrame:
    path = cache_dir / _cache_symbol_dir_name(symbol) / timeframe / "data.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_parquet(path)
    frame.sort_values("timestamp", inplace=True)
    frame.drop_duplicates("timestamp", keep="last", inplace=True)
    frame.reset_index(drop=True, inplace=True)
    if "ema20" not in frame.columns and "close" in frame.columns:
        frame["ema20"] = frame["close"].astype(float).ewm(span=20, adjust=False).mean()
    return frame


def _aggregate_frame_to_timeframe(frame: pd.DataFrame, *, timeframe_ms: int) -> pd.DataFrame:
    if frame.empty or timeframe_ms <= 0:
        return pd.DataFrame()
    required = {"timestamp", "open", "high", "low", "close"}
    if required.difference(frame.columns):
        return pd.DataFrame()
    prepared = frame.copy()
    for column in ("timestamp", "open", "high", "low", "close", "volume", "quote_volume", "number_of_trades", "taker_buy_volume", "taker_buy_quote_volume"):
        if column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close"])
    if prepared.empty:
        return pd.DataFrame()
    prepared["timestamp"] = prepared["timestamp"].astype("int64")
    prepared.sort_values("timestamp", inplace=True)
    prepared.drop_duplicates("timestamp", keep="last", inplace=True)
    prepared["bucket"] = (prepared["timestamp"] // int(timeframe_ms)) * int(timeframe_ms)
    aggregation: dict[str, str] = {
        "timestamp": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_volume", "taker_buy_quote_volume"):
        if column in prepared.columns:
            aggregation[column] = "sum"
    aggregated = prepared.groupby("bucket", as_index=False).agg(aggregation)
    aggregated["timestamp"] = aggregated["bucket"].astype("int64")
    aggregated.drop(columns=["bucket"], inplace=True)
    aggregated.sort_values("timestamp", inplace=True)
    aggregated.reset_index(drop=True, inplace=True)
    if "ema20" not in aggregated.columns and "close" in aggregated.columns:
        aggregated["ema20"] = aggregated["close"].astype(float).ewm(span=20, adjust=False).mean()
    return aggregated


def _materialized_entry_flow_source(frame: pd.DataFrame, *, entry_timeframe: str) -> str:
    if "aggregation_source_timeframe" not in frame.columns:
        return "cached_ohlcv"
    source = str(frame["aggregation_source_timeframe"].dropna().iloc[0]) if not frame.empty else ""
    version = (
        str(frame["aggregation_version"].dropna().iloc[0])
        if "aggregation_version" in frame.columns and not frame.empty
        else ""
    )
    if source == "1s" and version == _MATERIALIZED_SUBMINUTE_CACHE_VERSION:
        return f"cached_1s_aggregated_to_{entry_timeframe}"
    return "cached_ohlcv"


def materialize_subminute_entry_caches(
    *,
    cache_dir: Path,
    target_timeframes: Iterable[str],
    symbols: Iterable[str] | None = None,
    overwrite: bool = False,
    progress_label: str | None = None,
) -> pd.DataFrame:
    wanted_symbols = set(symbols) if symbols is not None else None
    targets = tuple(dict.fromkeys(str(timeframe) for timeframe in target_timeframes))
    for target in targets:
        target_ms = _timeframe_to_milliseconds(target)
        if target == "1s" or target_ms <= 0 or target_ms >= 60_000 or target_ms % 1000 != 0:
            raise ValueError(f"target subminute timeframe must be derived from 1s: {target}")
    paths = sorted(cache_dir.glob("*%2FUSDT%3AUSDT/1s/data.parquet"))
    if wanted_symbols is not None:
        paths = [path for path in paths if _symbol_from_cache_symbol_dir(path.parent.parent) in wanted_symbols]
    rows: list[dict[str, object]] = []
    started_at = time.monotonic()
    next_progress_pct = 0
    total = max(1, len(paths) * max(1, len(targets)))
    done = 0
    for path in paths:
        symbol = _symbol_from_cache_symbol_dir(path.parent.parent)
        try:
            source_frame = _read_symbol_frame(cache_dir, symbol, "1s")
        except Exception as exc:
            for target in targets:
                rows.append(
                    {
                        "symbol": symbol,
                        "target_timeframe": target,
                        "source_timeframe": "1s",
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                done += 1
            continue
        for target in targets:
            done += 1
            output_path = cache_dir / _cache_symbol_dir_name(symbol) / target / "data.parquet"
            if output_path.exists() and not overwrite:
                rows.append(
                    {
                        "symbol": symbol,
                        "target_timeframe": target,
                        "source_timeframe": "1s",
                        "status": "exists",
                        "path": str(output_path),
                    }
                )
            else:
                aggregated = _aggregate_frame_to_timeframe(
                    source_frame,
                    timeframe_ms=_timeframe_to_milliseconds(target),
                )
                if aggregated.empty:
                    rows.append(
                        {
                            "symbol": symbol,
                            "target_timeframe": target,
                            "source_timeframe": "1s",
                            "status": "empty",
                            "path": str(output_path),
                        }
                    )
                else:
                    aggregated = aggregated.copy()
                    aggregated["aggregation_source_timeframe"] = "1s"
                    aggregated["aggregation_target_timeframe"] = target
                    aggregated["aggregation_version"] = _MATERIALIZED_SUBMINUTE_CACHE_VERSION
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    aggregated.to_parquet(output_path, index=False)
                    rows.append(
                        {
                            "symbol": symbol,
                            "target_timeframe": target,
                            "source_timeframe": "1s",
                            "status": "written",
                            "path": str(output_path),
                            "rows": int(len(aggregated)),
                            "min_timestamp_ms": int(aggregated["timestamp"].min()),
                            "max_timestamp_ms": int(aggregated["timestamp"].max()),
                            "min_timestamp_utc": _timestamp_to_utc(int(aggregated["timestamp"].min())),
                            "max_timestamp_utc": _timestamp_to_utc(int(aggregated["timestamp"].max())),
                        }
                    )
            if progress_label is not None:
                current_pct = int(100 * done / total)
                if current_pct >= next_progress_pct or done == total:
                    _emit_progress(label=progress_label, done=done, total=total, started_at=started_at)
                    next_progress_pct = current_pct + 5
    return pd.DataFrame(rows)


def build_entry_cache_coverage(
    *,
    cache_dir: Path,
    setup_timeframe: str,
    entry_timeframe: str,
    start_ms: int,
    end_ms: int,
    symbols: Iterable[str] | None = None,
) -> pd.DataFrame:
    wanted_symbols = set(symbols) if symbols is not None else None
    rows: list[dict[str, object]] = []
    for requested, cache_timeframe, role in (
        (setup_timeframe, setup_timeframe, "setup"),
        (entry_timeframe, _resolve_entry_cache_timeframe(cache_dir, entry_timeframe), "entry"),
    ):
        symbol_rows: list[tuple[str, int, int, int]] = []
        for path in cache_dir.glob(f"*%2FUSDT%3AUSDT/{cache_timeframe}/data.parquet"):
            symbol = _symbol_from_cache_symbol_dir(path.parent.parent)
            if wanted_symbols is not None and symbol not in wanted_symbols:
                continue
            try:
                timestamps = pd.read_parquet(path, columns=["timestamp"])["timestamp"]
            except Exception:
                continue
            if timestamps.empty:
                continue
            symbol_rows.append((symbol, int(timestamps.min()), int(timestamps.max()), int(len(timestamps))))
        if symbol_rows:
            min_ts = min(row[1] for row in symbol_rows)
            max_ts = max(row[2] for row in symbol_rows)
            rows.append(
                {
                    "role": role,
                    "requested_timeframe": requested,
                    "cache_timeframe": cache_timeframe,
                    "symbols": len(symbol_rows),
                    "rows": sum(row[3] for row in symbol_rows),
                    "global_min_timestamp_ms": min_ts,
                    "global_max_timestamp_ms": max_ts,
                    "global_min_timestamp_utc": _timestamp_to_utc(min_ts),
                    "global_max_timestamp_utc": _timestamp_to_utc(max_ts),
                    "requested_start_timestamp_ms": int(start_ms),
                    "requested_end_timestamp_ms": int(end_ms),
                    "requested_start_timestamp_utc": _timestamp_to_utc(int(start_ms)),
                    "requested_end_timestamp_utc": _timestamp_to_utc(int(end_ms)),
                    "symbols_covering_start": sum(1 for _, min_symbol_ts, _, _ in symbol_rows if min_symbol_ts <= start_ms),
                    "symbols_covering_end": sum(1 for _, _, max_symbol_ts, _ in symbol_rows if max_symbol_ts >= end_ms),
                }
            )
        else:
            rows.append(
                {
                    "role": role,
                    "requested_timeframe": requested,
                    "cache_timeframe": cache_timeframe,
                    "symbols": 0,
                    "rows": 0,
                    "requested_start_timestamp_ms": int(start_ms),
                    "requested_end_timestamp_ms": int(end_ms),
                    "requested_start_timestamp_utc": _timestamp_to_utc(int(start_ms)),
                    "requested_end_timestamp_utc": _timestamp_to_utc(int(end_ms)),
                }
            )
    return pd.DataFrame(rows)


def enrich_candidates_with_recent_spike_context(
    candidates: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
) -> pd.DataFrame:
    if candidates.empty or "symbol" not in candidates.columns or "decision_timestamp_ms" not in candidates.columns:
        return candidates
    result = candidates.copy()
    for column in (
        "prior_spike_count_24h",
        "prior_spike_count_72h",
        "prior_fast_fade_count_24h",
        "prior_fast_fade_count_72h",
        "prior_big_move_count_24h",
        "prior_big_move_count_72h",
        "prior_spike_density_72h",
        "time_since_prior_spike_ms",
        "time_since_prior_spike_hours",
    ):
        result[column] = np.nan
    entry_ms = _timeframe_to_milliseconds(_effective_entry_timeframe(config))
    maturity_ms = max(config.lab_config.forward_high_candles, config.lab_config.forward_low_candles) * entry_ms
    for _, group in result.groupby("symbol", sort=False):
        ordered = group.copy()
        ordered["_decision_ts_numeric"] = pd.to_numeric(ordered["decision_timestamp_ms"], errors="coerce")
        ordered = ordered.dropna(subset=["_decision_ts_numeric"]).sort_values("_decision_ts_numeric")
        if ordered.empty:
            continue
        timestamps = ordered["_decision_ts_numeric"].astype("int64").to_numpy()
        labels = (
            ordered["outcome_label"].astype(str).to_numpy()
            if "outcome_label" in ordered.columns
            else np.array([""] * len(ordered), dtype=object)
        )
        for pos, row_index in enumerate(ordered.index):
            decision_ts = int(timestamps[pos])
            prior_end = int(np.searchsorted(timestamps, decision_ts, side="left"))
            prior_start_24h = int(np.searchsorted(timestamps, decision_ts - _DAY_MS, side="left"))
            prior_start_72h = int(np.searchsorted(timestamps, decision_ts - 3 * _DAY_MS, side="left"))
            mature_cutoff = decision_ts - maturity_ms
            mature_end = int(np.searchsorted(timestamps, mature_cutoff, side="right"))
            mature_24h_start = min(prior_start_24h, mature_end)
            mature_72h_start = min(prior_start_72h, mature_end)
            mature_labels_24h = labels[mature_24h_start:mature_end]
            mature_labels_72h = labels[mature_72h_start:mature_end]
            prior_count_24h = max(0, prior_end - prior_start_24h)
            prior_count_72h = max(0, prior_end - prior_start_72h)
            result.at[row_index, "prior_spike_count_24h"] = prior_count_24h
            result.at[row_index, "prior_spike_count_72h"] = prior_count_72h
            result.at[row_index, "prior_fast_fade_count_24h"] = int((mature_labels_24h == "fast_fade").sum())
            result.at[row_index, "prior_fast_fade_count_72h"] = int((mature_labels_72h == "fast_fade").sum())
            result.at[row_index, "prior_big_move_count_24h"] = int((mature_labels_24h == "big_move").sum())
            result.at[row_index, "prior_big_move_count_72h"] = int((mature_labels_72h == "big_move").sum())
            result.at[row_index, "prior_spike_density_72h"] = _safe_divide_value(prior_count_72h, 3.0)
            if prior_end > 0:
                elapsed_ms = int(decision_ts - timestamps[prior_end - 1])
                result.at[row_index, "time_since_prior_spike_ms"] = elapsed_ms
                result.at[row_index, "time_since_prior_spike_hours"] = _safe_divide_value(elapsed_ms, _HOUR_MS)
    return result


def _resolve_entry_cache_timeframe(cache_dir: Path, entry_timeframe: str) -> str:
    if any(cache_dir.glob(f"*%2FUSDT%3AUSDT/{entry_timeframe}/data.parquet")):
        return entry_timeframe
    entry_ms = _timeframe_to_milliseconds(entry_timeframe)
    if 0 < entry_ms < 60_000 and entry_ms % 1000 == 0 and any(cache_dir.glob("*%2FUSDT%3AUSDT/1s/data.parquet")):
        return "1s"
    return entry_timeframe


def _read_entry_simulation_frame(cache_dir: Path, symbol: str, entry_timeframe: str) -> pd.DataFrame:
    entry_cache_timeframe = _resolve_entry_cache_timeframe(cache_dir, entry_timeframe)
    frame = _read_symbol_frame(cache_dir, symbol, entry_cache_timeframe)
    if entry_cache_timeframe == entry_timeframe:
        return frame
    aggregated = _aggregate_frame_to_timeframe(
        frame,
        timeframe_ms=_timeframe_to_milliseconds(entry_timeframe),
    )
    if aggregated.empty:
        raise ValueError(f"empty_1s_aggregation_for_entry_timeframe:{entry_timeframe}")
    return aggregated


def build_anomaly_signals(
    candidates: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
) -> pd.DataFrame:
    config = _apply_red_flag_profile(config)
    if candidates.empty:
        return pd.DataFrame()
    required = {
        "price_retention_next_n",
        "start_verticality_score",
        "hold_count_next_n_candles",
        "decision_close",
        "decision_box_low",
        "decision_box_high",
        "decision_box_range",
        "decision_ema20",
        "timestamp_ms",
        "decision_timestamp_ms",
    }
    if config.min_oi_change_pct_3x5m is not None:
        required.add("oi_change_pct_3x5m")
    if config.require_oi_status_ok:
        required.add("oi_status")
    optional_filter_columns = {
        "max_start_quote_ratio": "start_quote_ratio",
        "max_start_trade_ratio": "start_trade_ratio",
        "max_start_avg_trade_quote_size_ratio": "start_avg_trade_quote_size_ratio",
        "max_start_quote_ratio_per_abs_return": "start_quote_ratio_per_abs_return",
        "max_start_range_pct_ratio_to_baseline": "start_range_pct_ratio_to_baseline",
        "max_prior_up_down_whipsaw_to_impulse_range": "prior_up_down_whipsaw_to_impulse_range",
        "min_flow_hold_count": "flow_hold_count_next_n_candles",
        "max_prior_spike_count_72h": "prior_spike_count_72h",
        "max_prior_fast_fade_count_72h": "prior_fast_fade_count_72h",
        "min_start_lower_wick_to_range": "start_lower_wick_to_range",
        "max_start_upper_wick_to_range": "start_upper_wick_to_range",
        "min_next_taker_buy_quote_share": "next_n_taker_buy_quote_share_mean",
        "max_price_retention": "price_retention_next_n",
        "min_mark_close_vs_decision_close_basis": "mark_close_vs_decision_close_basis",
        "max_start_taker_buy_quote_share_delta": "start_taker_buy_quote_share_delta",
        "max_next_taker_buy_quote_share_delta": "next_n_taker_buy_quote_share_delta",
        "max_start_trade_ratio_per_abs_return": "start_trade_ratio_per_abs_return",
    }
    for config_field, column in optional_filter_columns.items():
        if getattr(config, config_field) is not None:
            required.add(column)
    if config.reject_oi_down_mark_discount:
        required.add("mark_close_vs_decision_close_basis")
        required.add("oi_price_interaction_3x5m")
    if config.reject_stale_derivatives_context:
        context_columns = _context_status_columns(candidates)
        if not context_columns:
            required.add("mark_status")
    missing = required.difference(candidates.columns)
    if missing:
        if "status" in candidates.columns and candidates["status"].astype(str).eq("error").any():
            return pd.DataFrame()
        raise ValueError(f"candidates missing required columns: {sorted(missing)}")

    signals = candidates.copy()
    signals["entry_price"] = signals["decision_close"].astype(float)
    box_low = signals["decision_box_low"].astype(float)
    box_range = signals["decision_box_range"].astype(float).clip(lower=0.0)
    decision_ema20 = signals["decision_ema20"].astype(float)
    previous_stop = box_low - config.stop_buffer_range_fraction * box_range
    signals["initial_stop_at_decision"] = pd.concat([previous_stop, decision_ema20], axis=1).max(axis=1)
    signals["initial_risk_pct_at_decision"] = (
        (signals["entry_price"] - signals["initial_stop_at_decision"]) / signals["entry_price"]
    )
    mask = (
        signals["price_retention_next_n"].astype(float).ge(config.min_price_retention)
        & signals["start_verticality_score"].astype(float).ge(config.min_verticality_score)
        & signals["hold_count_next_n_candles"].astype(float).ge(config.min_hold_count)
        & signals["initial_risk_pct_at_decision"].gt(0.0)
        & signals["initial_risk_pct_at_decision"].le(config.max_initial_risk_pct)
    )
    if config.min_oi_change_pct_3x5m is not None:
        mask &= signals["oi_change_pct_3x5m"].astype(float).gt(config.min_oi_change_pct_3x5m)
    if config.require_oi_status_ok:
        mask &= signals["oi_status"].eq("ok")
    if config.max_price_retention is not None:
        mask &= signals["price_retention_next_n"].astype(float).le(config.max_price_retention)
    if config.max_start_quote_ratio is not None:
        mask &= signals["start_quote_ratio"].astype(float).le(config.max_start_quote_ratio)
    if config.max_start_trade_ratio is not None:
        mask &= signals["start_trade_ratio"].astype(float).le(config.max_start_trade_ratio)
    if config.max_start_avg_trade_quote_size_ratio is not None:
        mask &= signals["start_avg_trade_quote_size_ratio"].astype(float).le(config.max_start_avg_trade_quote_size_ratio)
    if config.max_start_quote_ratio_per_abs_return is not None:
        mask &= signals["start_quote_ratio_per_abs_return"].astype(float).le(
            config.max_start_quote_ratio_per_abs_return
        )
    if config.max_start_range_pct_ratio_to_baseline is not None:
        mask &= signals["start_range_pct_ratio_to_baseline"].astype(float).le(
            config.max_start_range_pct_ratio_to_baseline
        )
    if config.max_prior_up_down_whipsaw_to_impulse_range is not None:
        mask &= signals["prior_up_down_whipsaw_to_impulse_range"].astype(float).le(
            config.max_prior_up_down_whipsaw_to_impulse_range
        )
    if config.min_next_taker_buy_quote_share is not None:
        mask &= signals["next_n_taker_buy_quote_share_mean"].astype(float).ge(
            config.min_next_taker_buy_quote_share
        )
    if config.min_flow_hold_count is not None:
        mask &= signals["flow_hold_count_next_n_candles"].astype(float).ge(config.min_flow_hold_count)
    if config.max_prior_spike_count_72h is not None:
        mask &= signals["prior_spike_count_72h"].fillna(0.0).astype(float).le(config.max_prior_spike_count_72h)
    if config.max_prior_fast_fade_count_72h is not None:
        mask &= (
            signals["prior_fast_fade_count_72h"]
            .fillna(0.0)
            .astype(float)
            .le(config.max_prior_fast_fade_count_72h)
        )
    if config.min_start_lower_wick_to_range is not None:
        mask &= signals["start_lower_wick_to_range"].astype(float).gt(config.min_start_lower_wick_to_range)
    if config.max_start_upper_wick_to_range is not None:
        mask &= signals["start_upper_wick_to_range"].astype(float).le(config.max_start_upper_wick_to_range)
    for red_flag_mask in _red_flag_violation_masks(signals, config=config).values():
        mask &= ~red_flag_mask.fillna(True)
    signals = signals.loc[mask].copy()
    if signals.empty:
        return signals
    signals.sort_values(["symbol", "decision_timestamp_ms"], inplace=True)
    signals.reset_index(drop=True, inplace=True)
    return signals


def _resolve_signal_entry(
    frame: pd.DataFrame,
    *,
    anomaly_timestamp_ms: int,
    decision_timestamp_ms: int,
    decision_close: float,
    config: AnomalyBacktestConfig,
) -> tuple[int, float, float, float, float, float, str]:
    box = frame.loc[
        (frame["timestamp"] >= anomaly_timestamp_ms)
        & (frame["timestamp"] <= decision_timestamp_ms)
    ]
    if box.empty:
        return decision_timestamp_ms, float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), "empty_decision_box"
    box_low = float(box["low"].min())
    box_high = float(box["high"].max())
    box_range = max(box_high - box_low, 0.0)
    previous_stop = box_low - config.stop_buffer_range_fraction * box_range
    decision_row = frame.loc[frame["timestamp"].eq(decision_timestamp_ms)]
    decision_ema20 = (
        _safe_float(decision_row["ema20"].iloc[0])
        if not decision_row.empty and "ema20" in decision_row.columns
        else None
    )
    stop_at_decision = max(previous_stop, decision_ema20) if decision_ema20 is not None else previous_stop
    signal_risk = decision_close - stop_at_decision
    base_signal_tp1_price = decision_close + config.tp1_r * signal_risk if np.isfinite(signal_risk) else float("nan")
    signal_tp1_price, _signal_tp1_round_step = _round_up_tp1_to_market_number(
        base_signal_tp1_price,
        reference_price=decision_close,
        movement=max(signal_risk, box_range),
    )

    if config.entry_method == "market":
        if config.market_entry_latency_candles < 1:
            raise ValueError("market_entry_latency_candles must be >= 1")
        future = frame.loc[frame["timestamp"] > decision_timestamp_ms].head(config.market_entry_latency_candles)
        if len(future) < config.market_entry_latency_candles:
            return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "no_market_execution_candle"
        entry_row_source = future.iloc[-1]
        entry_ts = int(entry_row_source["timestamp"])
        entry_price = float(entry_row_source["open"])
        if not np.isfinite(entry_price) or entry_price <= 0.0:
            return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "invalid_market_execution_price"
        drift_pct = _safe_divide_value(entry_price - decision_close, decision_close)
        abs_drift_pct = abs(drift_pct) if np.isfinite(drift_pct) else float("nan")
        actual_risk_at_signal_stop = entry_price - stop_at_decision
        rr_to_signal_tp1 = _safe_divide_value(signal_tp1_price - entry_price, actual_risk_at_signal_stop)
        if entry_price >= signal_tp1_price:
            return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "tp1_already_reached_before_market_entry"
        if not np.isfinite(actual_risk_at_signal_stop) or actual_risk_at_signal_stop <= 0.0:
            return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "invalid_actual_market_risk"
        if not np.isfinite(abs_drift_pct) or abs_drift_pct > config.max_market_entry_drift_pct:
            return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "market_entry_price_drift"
        if not np.isfinite(rr_to_signal_tp1) or rr_to_signal_tp1 < config.min_market_rr_to_signal_tp1:
            return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "market_entry_rr_collapsed"
        initial_stop = stop_at_decision
        initial_risk = entry_price - initial_stop
        return entry_ts, entry_price, initial_stop, initial_risk, box_range, box_high, ""
    else:
        future = frame.loc[frame["timestamp"] > decision_timestamp_ms].head(config.entry_timeout_candles)
        if future.empty:
            return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "entry_timeout_no_future_candles"
        if config.entry_method == "break_box_high":
            trigger = box_high
            hit = future.loc[future["high"].astype(float).ge(trigger)]
            if hit.empty:
                return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "entry_trigger_not_reached"
            entry_ts = int(hit["timestamp"].iloc[0])
            entry_price = trigger
        elif config.entry_method == "pullback_box_fraction":
            trigger = box_low + config.pullback_box_fraction * box_range
            entry_ts = decision_timestamp_ms
            entry_price = float("nan")
            for _, row in future.iterrows():
                low = float(row["low"])
                high = float(row["high"])
                if low <= stop_at_decision:
                    return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "stop_touched_before_entry"
                if low <= trigger <= high:
                    entry_ts = int(row["timestamp"])
                    entry_price = trigger
                    break
            if not np.isfinite(entry_price):
                return decision_timestamp_ms, float("nan"), stop_at_decision, float("nan"), box_range, box_high, "entry_trigger_not_reached"
        else:
            raise ValueError(f"unsupported entry_method: {config.entry_method}")

    entry_row = frame.loc[frame["timestamp"].eq(entry_ts)]
    entry_ema20 = _safe_float(entry_row["ema20"].iloc[0]) if not entry_row.empty and "ema20" in entry_row.columns else None
    initial_stop = max(previous_stop, entry_ema20) if entry_ema20 is not None else previous_stop
    initial_risk = entry_price - initial_stop
    return entry_ts, entry_price, initial_stop, initial_risk, box_range, box_high, ""



def collect_pair_anomaly_rows(
    config: AnomalyBacktestConfig,
    *,
    symbols: Iterable[str] | None = None,
    progress_label: str | None = None,
    include_derivatives_context: bool = True,
) -> pd.DataFrame:
    setup_timeframe = _effective_setup_timeframe(config)
    entry_timeframe = _effective_entry_timeframe(config)
    entry_cache_timeframe = _resolve_entry_cache_timeframe(config.lab_config.cache_dir, entry_timeframe)
    lab_config = config.lab_config
    end_ms = lab_config.end_timestamp_ms
    if end_ms is None:
        # Use entry timeframe as the source of truth for executable decisions.
        max_timestamp: int | None = None
        for path in lab_config.cache_dir.glob(f"*/{entry_cache_timeframe}/data.parquet"):
            try:
                timestamps = pd.read_parquet(path, columns=["timestamp"])
            except Exception:
                continue
            if timestamps.empty:
                continue
            current = int(timestamps["timestamp"].max())
            max_timestamp = current if max_timestamp is None else max(max_timestamp, current)
        if max_timestamp is None:
            end_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        else:
            end_ms = max_timestamp
    start_ms = int((datetime.fromtimestamp(int(end_ms) / 1000, UTC) - pd.Timedelta(days=lab_config.days)).timestamp() * 1000)
    wanted_symbols = set(symbols) if symbols is not None else None
    paths = sorted(lab_config.cache_dir.glob(f"*%2FUSDT%3AUSDT/{setup_timeframe}/data.parquet"))
    if wanted_symbols is not None:
        paths = [path for path in paths if _symbol_from_cache_symbol_dir(path.parent.parent) in wanted_symbols]
    if entry_timeframe != setup_timeframe and _timeframe_to_milliseconds(entry_timeframe) < 60_000:
        symbols_with_entry_cache = {
            _symbol_from_cache_symbol_dir(path.parent.parent)
            for path in lab_config.cache_dir.glob(f"*%2FUSDT%3AUSDT/{entry_cache_timeframe}/data.parquet")
        }
        if not symbols_with_entry_cache:
            return pd.DataFrame([
                {
                    "symbol": "__all__",
                    "timeframe": setup_timeframe,
                    "setup_timeframe": setup_timeframe,
                    "entry_timeframe": entry_timeframe,
                    "feature_contract": "htf_setup_ltf_entry_v1",
                    "status": "error",
                    "error": f"missing_subminute_entry_cache:{entry_timeframe}",
                    "execution_model": "requires_historical_aggtrades_cache",
                }
            ])
        paths = [path for path in paths if _symbol_from_cache_symbol_dir(path.parent.parent) in symbols_with_entry_cache]
    rows: list[dict[str, object]] = []
    progress_started_at = time.monotonic()
    next_progress_pct = 0
    for processed_count, path in enumerate(paths, start=1):
        symbol = _symbol_from_cache_symbol_dir(path.parent.parent)
        try:
            setup_frame = _read_symbol_frame(lab_config.cache_dir, symbol, setup_timeframe)
            entry_frame = _read_symbol_frame(lab_config.cache_dir, symbol, entry_cache_timeframe)
            setup_frame = setup_frame.loc[(setup_frame["timestamp"] >= start_ms - lab_config.baseline_candles * _timeframe_to_milliseconds(setup_timeframe)) & (setup_frame["timestamp"] <= int(end_ms))].copy()
            entry_frame = entry_frame.loc[(entry_frame["timestamp"] >= start_ms) & (entry_frame["timestamp"] <= int(end_ms))].copy()
            if entry_cache_timeframe != entry_timeframe:
                entry_frame = _aggregate_frame_to_timeframe(
                    entry_frame,
                    timeframe_ms=_timeframe_to_milliseconds(entry_timeframe),
                )
                entry_flow_source = f"cached_{entry_cache_timeframe}_aggregated_to_{entry_timeframe}"
            else:
                entry_flow_source = _materialized_entry_flow_source(entry_frame, entry_timeframe=entry_timeframe)
            rows.extend(_collect_symbol_pair_rows(
                symbol=symbol,
                setup_frame=setup_frame,
                entry_frame=entry_frame,
                config=config,
                entry_flow_source=entry_flow_source,
            ))
        except Exception as exc:
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": setup_timeframe,
                    "setup_timeframe": setup_timeframe,
                    "entry_timeframe": entry_timeframe,
                    "feature_contract": "htf_setup_ltf_entry_v1",
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if progress_label is not None and paths:
            current_pct = int(100 * processed_count / len(paths))
            if current_pct >= next_progress_pct or processed_count == len(paths):
                _emit_progress(label=progress_label, done=processed_count, total=len(paths), started_at=progress_started_at)
                next_progress_pct = current_pct + 5
    result = pd.DataFrame(rows)
    if not result.empty and "timestamp_ms" in result.columns:
        result.sort_values(["timestamp_ms", "symbol", "decision_timestamp_ms"], inplace=True)
        result.reset_index(drop=True, inplace=True)
    result = enrich_candidates_with_recent_spike_context(result, config=config)
    result = enrich_candidates_with_open_interest(result, cache_dir=lab_config.cache_dir, progress_label=f"{progress_label}: oi context" if progress_label is not None else None)
    if include_derivatives_context:
        result = enrich_candidates_with_derivatives_context(result, cache_dir=lab_config.cache_dir, progress_label=f"{progress_label}: derivatives context" if progress_label is not None else None)
    return result


def _pair_key(config: AnomalyBacktestConfig) -> tuple[str, str]:
    return (_effective_setup_timeframe(config), _effective_entry_timeframe(config))


def _resolve_pair_collection_window(config: AnomalyBacktestConfig) -> tuple[int, int, str]:
    lab_config = config.lab_config
    entry_timeframe = _effective_entry_timeframe(config)
    entry_cache_timeframe = _resolve_entry_cache_timeframe(lab_config.cache_dir, entry_timeframe)
    end_ms = lab_config.end_timestamp_ms
    if end_ms is None:
        max_timestamp: int | None = None
        for path in lab_config.cache_dir.glob(f"*/{entry_cache_timeframe}/data.parquet"):
            try:
                timestamps = pd.read_parquet(path, columns=["timestamp"])
            except Exception:
                continue
            if timestamps.empty:
                continue
            current = int(timestamps["timestamp"].max())
            max_timestamp = current if max_timestamp is None else max(max_timestamp, current)
        end_ms = int(datetime.now(tz=UTC).timestamp() * 1000) if max_timestamp is None else max_timestamp
    start_ms = int(
        (
            datetime.fromtimestamp(int(end_ms) / 1000, UTC)
            - pd.Timedelta(days=lab_config.days)
        ).timestamp()
        * 1000
    )
    return start_ms, int(end_ms), entry_cache_timeframe


def _cache_symbols_for_timeframe(cache_dir: Path, timeframe: str) -> set[str]:
    return {
        _symbol_from_cache_symbol_dir(path.parent.parent)
        for path in cache_dir.glob(f"*%2FUSDT%3AUSDT/{timeframe}/data.parquet")
    }


def collect_pair_anomaly_rows_for_configs(
    configs: Iterable[AnomalyBacktestConfig],
    *,
    symbols: Iterable[str] | None = None,
    progress_label: str | None = None,
    include_derivatives_context: bool = True,
) -> dict[tuple[str, str], pd.DataFrame]:
    """Collect pair candidates in one symbol-major pass across multiple TF sets."""

    resolved_configs = [_apply_red_flag_profile(config) for config in configs]
    if not resolved_configs:
        return {}

    wanted_symbols = set(symbols) if symbols is not None else None
    states: list[dict[str, object]] = []
    rows_by_key: dict[tuple[str, str], list[dict[str, object]]] = {}
    for config in resolved_configs:
        setup_timeframe, entry_timeframe = _pair_key(config)
        key = (setup_timeframe, entry_timeframe)
        rows_by_key.setdefault(key, [])
        start_ms, end_ms, entry_cache_timeframe = _resolve_pair_collection_window(config)
        setup_symbols = _cache_symbols_for_timeframe(config.lab_config.cache_dir, setup_timeframe)
        if wanted_symbols is not None:
            setup_symbols &= wanted_symbols
        entry_ms = _timeframe_to_milliseconds(entry_timeframe)
        eligible_symbols = set(setup_symbols)
        if entry_timeframe != setup_timeframe and entry_ms < 60_000:
            entry_symbols = _cache_symbols_for_timeframe(config.lab_config.cache_dir, entry_cache_timeframe)
            if wanted_symbols is not None:
                entry_symbols &= wanted_symbols
            if not entry_symbols:
                rows_by_key[key].append(
                    {
                        "symbol": "__all__",
                        "timeframe": setup_timeframe,
                        "setup_timeframe": setup_timeframe,
                        "entry_timeframe": entry_timeframe,
                        "feature_contract": "htf_setup_ltf_entry_v1",
                        "status": "error",
                        "error": f"missing_subminute_entry_cache:{entry_timeframe}",
                        "execution_model": "requires_historical_aggtrades_cache",
                    }
                )
                eligible_symbols = set()
            else:
                eligible_symbols &= entry_symbols
        states.append(
            {
                "config": config,
                "key": key,
                "setup_timeframe": setup_timeframe,
                "entry_timeframe": entry_timeframe,
                "entry_cache_timeframe": entry_cache_timeframe,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "eligible_symbols": eligible_symbols,
            }
        )

    all_symbols = sorted(
        {
            symbol
            for state in states
            for symbol in state["eligible_symbols"]  # type: ignore[union-attr]
        }
    )
    progress_started_at = time.monotonic()
    next_progress_pct = 0
    for processed_count, symbol in enumerate(all_symbols, start=1):
        symbol_states = [
            state
            for state in states
            if symbol in state["eligible_symbols"]  # type: ignore[operator]
        ]
        frame_cache: dict[str, pd.DataFrame] = {}
        frame_errors: dict[str, Exception] = {}
        required_timeframes = sorted(
            {
                str(state["setup_timeframe"])
                for state in symbol_states
            }
            | {
                str(state["entry_cache_timeframe"])
                for state in symbol_states
            }
        )
        cache_dir = resolved_configs[0].lab_config.cache_dir
        for timeframe in required_timeframes:
            try:
                frame_cache[timeframe] = _read_symbol_frame(cache_dir, symbol, timeframe)
            except Exception as exc:
                frame_errors[timeframe] = exc

        for state in symbol_states:
            config = state["config"]
            assert isinstance(config, AnomalyBacktestConfig)
            setup_timeframe = str(state["setup_timeframe"])
            entry_timeframe = str(state["entry_timeframe"])
            entry_cache_timeframe = str(state["entry_cache_timeframe"])
            key = state["key"]
            assert isinstance(key, tuple)
            try:
                if setup_timeframe in frame_errors:
                    raise frame_errors[setup_timeframe]
                if entry_cache_timeframe in frame_errors:
                    raise frame_errors[entry_cache_timeframe]
                start_ms = int(state["start_ms"])
                end_ms = int(state["end_ms"])
                setup_ms = _timeframe_to_milliseconds(setup_timeframe)
                setup_frame = frame_cache[setup_timeframe]
                entry_frame = frame_cache[entry_cache_timeframe]
                setup_frame = setup_frame.loc[
                    (setup_frame["timestamp"] >= start_ms - config.lab_config.baseline_candles * setup_ms)
                    & (setup_frame["timestamp"] <= end_ms)
                ].copy()
                entry_frame = entry_frame.loc[
                    (entry_frame["timestamp"] >= start_ms)
                    & (entry_frame["timestamp"] <= end_ms)
                ].copy()
                if entry_cache_timeframe != entry_timeframe:
                    entry_frame = _aggregate_frame_to_timeframe(
                        entry_frame,
                        timeframe_ms=_timeframe_to_milliseconds(entry_timeframe),
                    )
                    entry_flow_source = f"cached_{entry_cache_timeframe}_aggregated_to_{entry_timeframe}"
                else:
                    entry_flow_source = _materialized_entry_flow_source(
                        entry_frame,
                        entry_timeframe=entry_timeframe,
                    )
                rows_by_key[key].extend(
                    _collect_symbol_pair_rows(
                        symbol=symbol,
                        setup_frame=setup_frame,
                        entry_frame=entry_frame,
                        config=config,
                        entry_flow_source=entry_flow_source,
                    )
                )
            except Exception as exc:
                rows_by_key[key].append(
                    {
                        "symbol": symbol,
                        "timeframe": setup_timeframe,
                        "setup_timeframe": setup_timeframe,
                        "entry_timeframe": entry_timeframe,
                        "feature_contract": "htf_setup_ltf_entry_v1",
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        if progress_label is not None and all_symbols:
            current_pct = int(100 * processed_count / len(all_symbols))
            if current_pct >= next_progress_pct or processed_count == len(all_symbols):
                _emit_progress(
                    label=progress_label,
                    done=processed_count,
                    total=len(all_symbols),
                    started_at=progress_started_at,
                )
                next_progress_pct = current_pct + 5

    result_by_key: dict[tuple[str, str], pd.DataFrame] = {}
    for state in states:
        config = state["config"]
        assert isinstance(config, AnomalyBacktestConfig)
        key = state["key"]
        assert isinstance(key, tuple)
        result = pd.DataFrame(rows_by_key.get(key, []))
        if not result.empty and "timestamp_ms" in result.columns:
            result.sort_values(["timestamp_ms", "symbol", "decision_timestamp_ms"], inplace=True)
            result.reset_index(drop=True, inplace=True)
        result = enrich_candidates_with_recent_spike_context(result, config=config)
        context_label = f"{progress_label} {key[0]}/{key[1]}" if progress_label is not None else None
        result = enrich_candidates_with_open_interest(
            result,
            cache_dir=config.lab_config.cache_dir,
            progress_label=f"{context_label}: oi context" if context_label is not None else None,
        )
        if include_derivatives_context:
            result = enrich_candidates_with_derivatives_context(
                result,
                cache_dir=config.lab_config.cache_dir,
                progress_label=f"{context_label}: derivatives context" if context_label is not None else None,
            )
        result_by_key[key] = result
    return result_by_key


def _collect_symbol_pair_rows(
    *,
    symbol: str,
    setup_frame: pd.DataFrame,
    entry_frame: pd.DataFrame,
    config: AnomalyBacktestConfig,
    entry_flow_source: str = "cached_ohlcv",
) -> list[dict[str, object]]:
    lab_config = config.lab_config
    setup_timeframe = _effective_setup_timeframe(config)
    entry_timeframe = _effective_entry_timeframe(config)
    setup_ms = _timeframe_to_milliseconds(setup_timeframe)
    entry_ms = _timeframe_to_milliseconds(entry_timeframe)
    if setup_ms <= 0 or entry_ms <= 0 or entry_ms > setup_ms:
        raise ValueError(f"invalid setup/entry timeframe pair: {setup_timeframe}/{entry_timeframe}")
    if setup_frame.empty or entry_frame.empty:
        return []
    setup_frame = setup_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    entry_frame = entry_frame.copy().sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    rows: list[dict[str, object]] = []
    setup_timestamps = setup_frame["timestamp"].astype("int64").to_numpy()
    entry_timestamps = entry_frame["timestamp"].astype("int64").to_numpy()
    entry_max_timestamp = int(entry_timestamps[-1]) if len(entry_timestamps) else 0
    last_selected_setup_idx = -10**9
    max_forward = max(lab_config.forward_high_candles, lab_config.forward_low_candles)
    for setup_idx, setup_start in enumerate(setup_timestamps):
        setup_start = int(setup_start)
        if setup_idx < lab_config.baseline_candles:
            continue
        if setup_idx - last_selected_setup_idx < lab_config.cooldown_candles:
            continue
        baseline = setup_frame.iloc[setup_idx - lab_config.baseline_candles : setup_idx].copy()
        if baseline.empty:
            continue
        entry_start_pos = int(np.searchsorted(entry_timestamps, setup_start, side="left"))
        entry_end_pos_exclusive = int(np.searchsorted(entry_timestamps, setup_start + setup_ms, side="left"))
        entry_segment_full = entry_frame.iloc[entry_start_pos:entry_end_pos_exclusive]
        if len(entry_segment_full) < lab_config.confirmation_candles:
            continue
        selected_this_setup = False
        for entry_end_pos in range(lab_config.confirmation_candles - 1, len(entry_segment_full)):
            entry_segment = entry_segment_full.iloc[: entry_end_pos + 1].copy()
            decision = entry_segment.iloc[-1]
            decision_ts = int(decision["timestamp"])
            if decision_ts + max_forward * entry_ms >= entry_max_timestamp:
                continue
            forming_setup = _aggregate_ohlcv_to_candle(entry_segment, timestamp_ms=setup_start)
            if forming_setup is None:
                continue
            row = _build_pair_candidate_row(
                symbol=symbol,
                setup_timeframe=setup_timeframe,
                entry_timeframe=entry_timeframe,
                setup_ms=setup_ms,
                entry_ms=entry_ms,
                setup_idx=setup_idx,
                baseline=baseline,
                setup_row=forming_setup,
                entry_segment=entry_segment,
                entry_frame=entry_frame,
                config=config,
                entry_flow_source=entry_flow_source,
            )
            if row is None:
                continue
            rows.append(row)
            last_selected_setup_idx = setup_idx
            selected_this_setup = True
            break
        if selected_this_setup:
            continue
    return rows


def _build_pair_candidate_row(
    *,
    symbol: str,
    setup_timeframe: str,
    entry_timeframe: str,
    setup_ms: int,
    entry_ms: int,
    setup_idx: int,
    baseline: pd.DataFrame,
    setup_row: pd.Series,
    entry_segment: pd.DataFrame,
    entry_frame: pd.DataFrame,
    config: AnomalyBacktestConfig,
    entry_flow_source: str = "cached_ohlcv",
) -> dict[str, object] | None:
    lab_config = config.lab_config
    quote_volume = pd.to_numeric(baseline["quote_volume"], errors="coerce")
    trade_count = pd.to_numeric(baseline["number_of_trades"], errors="coerce")
    baseline_quote_value = float(quote_volume.median())
    baseline_trade_value = float(trade_count.median())
    start_quote = float(setup_row["quote_volume"])
    start_trades = float(setup_row["number_of_trades"])
    raw_start_quote_ratio = _safe_divide_value(start_quote, baseline_quote_value)
    raw_start_trade_ratio = _safe_divide_value(start_trades, baseline_trade_value)
    setup_elapsed_fraction = min(1.0, len(entry_segment) * entry_ms / setup_ms)
    elapsed_for_ratio = max(1e-9, setup_elapsed_fraction)
    start_quote_ratio = _safe_divide_value(raw_start_quote_ratio, elapsed_for_ratio)
    start_trade_ratio = _safe_divide_value(raw_start_trade_ratio, elapsed_for_ratio)
    min_raw_quote_ratio = lab_config.min_quote_ratio_start * min(1.0, max(0.35, elapsed_for_ratio))
    min_raw_trade_ratio = lab_config.min_trade_ratio_start * min(1.0, max(0.35, elapsed_for_ratio))
    if not np.isfinite(start_quote_ratio) or not np.isfinite(start_trade_ratio) or not np.isfinite(raw_start_quote_ratio) or not np.isfinite(raw_start_trade_ratio):
        return None
    if (
        start_quote_ratio < lab_config.min_quote_ratio_start
        or start_trade_ratio < lab_config.min_trade_ratio_start
        or raw_start_quote_ratio < min_raw_quote_ratio
        or raw_start_trade_ratio < min_raw_trade_ratio
    ):
        return None
    start_open = float(setup_row["open"])
    start_high = float(setup_row["high"])
    start_low = float(setup_row["low"])
    start_close = float(setup_row["close"])
    decision = entry_segment.iloc[-1]
    decision_ts = int(decision["timestamp"])
    decision_close = float(decision["close"])
    impulse_low = float(min(start_low, pd.to_numeric(entry_segment["low"], errors="coerce").min()))
    impulse_high = float(max(start_high, pd.to_numeric(entry_segment["high"], errors="coerce").max()))
    impulse_range = impulse_high - impulse_low
    if not np.isfinite(impulse_range) or impulse_range <= 0.0:
        return None
    activation_price = start_open + max(0.0, start_close - start_open) * 0.50
    hold_count = int(pd.to_numeric(entry_segment["close"], errors="coerce").ge(activation_price).sum())
    price_retention = _safe_divide_value(decision_close - start_open, impulse_high - start_open)
    verticality = compute_start_verticality_metrics(entry_segment)
    future = entry_frame.loc[entry_frame["timestamp"].astype("int64") > decision_ts]
    future_high = float(pd.to_numeric(future.head(lab_config.forward_high_candles)["high"], errors="coerce").max())
    future_low = float(pd.to_numeric(future.head(lab_config.forward_low_candles)["low"], errors="coerce").min())
    future_ret_high = _safe_divide_value(future_high - decision_close, decision_close)
    future_dd_low = _safe_divide_value(future_low - decision_close, decision_close)
    setup_with_current = pd.concat([baseline, pd.DataFrame([setup_row.to_dict()])], ignore_index=True)
    decision_ema20 = float(setup_with_current["close"].astype(float).ewm(span=20, adjust=False).mean().iloc[-1])
    range_series = baseline["high"].astype(float) - baseline["low"].astype(float)
    baseline_range = float(range_series.median())
    baseline_range_pct = float((range_series / baseline["close"].astype(float).replace(0.0, np.nan)).median())
    start_range = start_high - start_low
    start_ret = _safe_divide_value(start_close - start_open, start_open)
    abs_start_ret = abs(start_ret) if np.isfinite(start_ret) else float("nan")
    baseline_avg_trade_quote = _safe_divide_value(baseline_quote_value, baseline_trade_value)
    start_avg_trade_quote = _safe_divide_value(start_quote, start_trades)
    prior_whipsaw = _prior_up_down_whipsaw_to_impulse_range(baseline, impulse_range=impulse_range)
    flow_hold_count = int(
        (
            pd.to_numeric(entry_segment["quote_volume"], errors="coerce").ge(max(0.35 * start_quote, 3.0 * baseline_quote_value))
            & pd.to_numeric(entry_segment["number_of_trades"], errors="coerce").ge(max(0.35 * start_trades, 3.0 * baseline_trade_value))
        ).sum()
    )
    next_quote_mean = float(pd.to_numeric(entry_segment["quote_volume"], errors="coerce").mean())
    next_trade_mean = float(pd.to_numeric(entry_segment["number_of_trades"], errors="coerce").mean())
    taker_metrics = _pair_taker_metrics(entry_segment, baseline, baseline_quote_value)
    return {
        "symbol": symbol,
        "timeframe": setup_timeframe,
        "feature_contract": "htf_setup_ltf_entry_v1",
        "setup_timeframe": setup_timeframe,
        "entry_timeframe": entry_timeframe,
        "setup_source": "forming_htf_from_entry_tf_backtest",
        "setup_elapsed_fraction": setup_elapsed_fraction,
        "setup_closed_entry_candles": int(len(entry_segment)),
        "timestamp_ms": int(setup_row["timestamp"]),
        "timestamp_utc": _timestamp_to_utc(int(setup_row["timestamp"])),
        "decision_timestamp_ms": decision_ts,
        "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
        "confirmation_candles": int(lab_config.confirmation_candles),
        "baseline_candles": int(lab_config.baseline_candles),
        "start_open": start_open,
        "start_high": start_high,
        "start_low": start_low,
        "start_close": start_close,
        "decision_close": decision_close,
        "decision_ema20": decision_ema20,
        "start_quote_volume": start_quote,
        "start_trade_count": start_trades,
        "baseline_quote_volume_median": baseline_quote_value,
        "baseline_trade_count_median": baseline_trade_value,
        "start_quote_ratio": start_quote_ratio,
        "start_trade_ratio": start_trade_ratio,
        "start_quote_ratio_raw": raw_start_quote_ratio,
        "start_trade_ratio_raw": raw_start_trade_ratio,
        "start_quote_pace_ratio": start_quote_ratio,
        "start_trade_pace_ratio": start_trade_ratio,
        "next_n_quote_volume_mean": next_quote_mean,
        "next_n_trade_count_mean": next_trade_mean,
        "next_n_quote_decay": _safe_divide_value(next_quote_mean, start_quote),
        "next_n_trade_decay": _safe_divide_value(next_trade_mean, start_trades),
        "hold_count_next_n_candles": hold_count,
        "hold_ratio_next_n_candles": _safe_divide_value(hold_count, len(entry_segment)),
        "hold_count_model": "entry_price_activation_hold",
        "flow_hold_count_next_n_candles": flow_hold_count,
        "flow_hold_ratio_next_n_candles": _safe_divide_value(flow_hold_count, len(entry_segment)),
        "price_retention_next_n": price_retention,
        "price_retention_model": "decision_close_vs_setup_open_to_high",
        "entry_activation_price": activation_price,
        "midpoint_lost_next_n": bool(decision_close < activation_price),
        "new_high_count_next_n": int(pd.to_numeric(entry_segment["high"], errors="coerce").gt(float(entry_segment.iloc[0]["high"])).sum()),
        "decision_box_low": impulse_low,
        "decision_box_high": impulse_high,
        "decision_box_range": impulse_range,
        "future_high": future_high,
        "future_low": future_low,
        "future_ret_high_after_decision": future_ret_high,
        "future_dd_low_after_decision": future_dd_low,
        "outcome_label": _classify_pair_outcome(future_ret_high=future_ret_high, future_dd_low=future_dd_low, config=lab_config),
        **{
            **_TRADE_CHART_FLOW_PROVENANCE,
            "entry_trade_count_source": f"{entry_flow_source}.number_of_trades",
            "entry_quote_volume_source": f"{entry_flow_source}.quote_volume",
        },
        **verticality,
        **taker_metrics,
        "start_avg_trade_quote_size": start_avg_trade_quote,
        "baseline_avg_trade_quote_size_median": baseline_avg_trade_quote,
        "start_avg_trade_quote_size_ratio": _safe_divide_value(start_avg_trade_quote, baseline_avg_trade_quote),
        "next_n_avg_trade_quote_size_mean": _safe_divide_value(next_quote_mean, next_trade_mean),
        "next_n_avg_trade_quote_size_decay": _safe_divide_value(_safe_divide_value(next_quote_mean, next_trade_mean), start_avg_trade_quote),
        "decision_return_from_start_open": _safe_divide_value(decision_close - start_open, start_open),
        "start_close_position_in_range": _safe_divide_value(start_close - start_low, start_range),
        "start_body_to_range": _safe_divide_value(abs(start_close - start_open), start_range),
        "start_upper_wick_to_range": _safe_divide_value(start_high - max(start_open, start_close), start_range),
        "start_lower_wick_to_range": _safe_divide_value(min(start_open, start_close) - start_low, start_range),
        "baseline_range_median": baseline_range,
        "baseline_range_pct_median": baseline_range_pct,
        "baseline_zero_range_share": float(range_series.le(0.0).mean()),
        "baseline_return_range_pct": _safe_divide_value(float(baseline["high"].max()) - float(baseline["low"].min()), start_open),
        "baseline_close_return_range_pct": _safe_divide_value(float(baseline["close"].max()) - float(baseline["close"].min()), start_open),
        "baseline_return_from_first_close_pct": _safe_divide_value(float(baseline["close"].iloc[-1]) - float(baseline["close"].iloc[0]), float(baseline["close"].iloc[0])),
        "prior_up_leg_to_impulse_range": float("nan"),
        "prior_down_leg_to_impulse_range": float("nan"),
        "prior_up_down_whipsaw_to_impulse_range": prior_whipsaw,
        "start_range_ratio_to_baseline": _safe_divide_value(start_range, baseline_range),
        "start_range_pct": _safe_divide_value(start_range, start_open),
        "start_range_pct_ratio_to_baseline": _safe_divide_value(_safe_divide_value(start_range, start_open), baseline_range_pct),
        "start_quote_per_abs_return": _safe_divide_value(start_quote, abs_start_ret),
        "start_trades_per_abs_return": _safe_divide_value(start_trades, abs_start_ret),
        "start_quote_ratio_per_abs_return": _safe_divide_value(start_quote_ratio, abs_start_ret),
        "start_trade_ratio_per_abs_return": _safe_divide_value(start_trade_ratio, abs_start_ret),
        "post_start_pullback_fraction_of_box": _safe_divide_value(impulse_high - float(pd.to_numeric(entry_segment["low"], errors="coerce").min()), impulse_range),
    }


def _pair_taker_metrics(entry_segment: pd.DataFrame, baseline: pd.DataFrame, baseline_quote_value: float) -> dict[str, object]:
    default = {
        "flow_taker_buy_status": "missing_columns",
        "start_taker_buy_quote_share": float("nan"),
        "baseline_taker_buy_quote_share_median": float("nan"),
        "start_taker_buy_quote_share_delta": float("nan"),
        "next_n_taker_buy_quote_share_mean": float("nan"),
        "next_n_taker_buy_quote_share_delta": float("nan"),
        "next_n_taker_buy_quote_share_decay": float("nan"),
    }
    if "taker_buy_quote_volume" not in entry_segment.columns:
        return default
    quote_volume = pd.to_numeric(entry_segment["quote_volume"], errors="coerce")
    taker_quote = pd.to_numeric(entry_segment["taker_buy_quote_volume"], errors="coerce")
    share = taker_quote / quote_volume.replace(0.0, np.nan)
    start_share = float(share.iloc[0])
    next_share = float(share.mean())
    baseline_share = float("nan")
    if "taker_buy_quote_volume" in baseline.columns:
        baseline_quote = pd.to_numeric(baseline["quote_volume"], errors="coerce")
        baseline_taker = pd.to_numeric(baseline["taker_buy_quote_volume"], errors="coerce")
        baseline_share = float((baseline_taker / baseline_quote.replace(0.0, np.nan)).median())
    return {
        "flow_taker_buy_status": "ok" if np.isfinite(start_share) else "missing_values",
        "start_taker_buy_quote_share": start_share,
        "baseline_taker_buy_quote_share_median": baseline_share,
        "start_taker_buy_quote_share_delta": start_share - baseline_share,
        "next_n_taker_buy_quote_share_mean": next_share,
        "next_n_taker_buy_quote_share_delta": next_share - baseline_share,
        "next_n_taker_buy_quote_share_decay": _safe_divide_value(next_share, start_share),
    }


def _classify_pair_outcome(*, future_ret_high: float, future_dd_low: float, config: AnomalyLabConfig) -> str:
    if np.isfinite(future_ret_high) and future_ret_high >= config.big_move_threshold:
        return "big_25p"
    if np.isfinite(future_ret_high) and np.isfinite(future_dd_low) and future_ret_high < config.fade_max_upside and future_dd_low <= config.fade_drawdown_threshold:
        return "fast_fade"
    return "other"


def _entry_delay_candles(
    frame: pd.DataFrame,
    *,
    entry_timestamp_ms: int,
    decision_timestamp_ms: int,
) -> int:
    frame_step_ms = int(infer_frame_step_ms(frame) or 60_000)
    if frame_step_ms <= 0:
        frame_step_ms = 60_000
    return max(0, int(round((int(entry_timestamp_ms) - int(decision_timestamp_ms)) / frame_step_ms)))


def simulate_long_signal(
    frame: pd.DataFrame,
    signal: pd.Series,
    *,
    config: AnomalyBacktestConfig,
) -> dict[str, object]:
    symbol = str(signal["symbol"])
    execution_model = _execution_model_label(config)
    anomaly_ts = int(signal["timestamp_ms"])
    decision_ts = int(signal["decision_timestamp_ms"])
    decision_close = float(signal["decision_close"])
    entry_ts, entry_price, initial_stop, initial_risk, box_range, box_high, entry_skip_reason = _resolve_signal_entry(
        frame,
        anomaly_timestamp_ms=anomaly_ts,
        decision_timestamp_ms=decision_ts,
        decision_close=decision_close,
        config=config,
    )
    if not np.isfinite(entry_price):
        return {
            "symbol": symbol,
            "decision_timestamp_ms": decision_ts,
            "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
            "status": "skipped",
            "skip_reason": entry_skip_reason or "entry_trigger_not_reached",
            "entry_method": config.entry_method,
            "execution_model": execution_model,
        }
    if not np.isfinite(initial_risk) or initial_risk <= 0.0:
        return {
            "symbol": symbol,
            "decision_timestamp_ms": decision_ts,
            "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
            "status": "skipped",
            "skip_reason": "invalid_initial_risk",
            "entry_method": config.entry_method,
            "execution_model": execution_model,
        }
    initial_risk_pct = initial_risk / entry_price
    if initial_risk_pct > config.max_initial_risk_pct:
        return {
            "symbol": symbol,
            "decision_timestamp_ms": decision_ts,
            "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
            "status": "skipped",
            "skip_reason": "initial_risk_too_wide",
            "initial_risk_pct": initial_risk_pct,
            "entry_method": config.entry_method,
            "execution_model": execution_model,
        }

    future = frame.loc[frame["timestamp"] > entry_ts].head(config.max_hold_candles).copy()
    if future.empty:
        return {
            "symbol": symbol,
            "decision_timestamp_ms": decision_ts,
            "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
            "status": "skipped",
            "skip_reason": "no_future_candles",
            "entry_method": config.entry_method,
            "execution_model": execution_model,
        }

    base_tp1_price = entry_price + config.tp1_r * initial_risk
    tp1_price, tp1_round_step = _round_up_tp1_to_market_number(
        base_tp1_price,
        reference_price=entry_price,
        movement=max(initial_risk, box_range),
    )
    active_stop = initial_stop
    tp1_hit = False
    remaining_fraction = 1.0
    realized_r = 0.0
    max_high = entry_price
    min_low = entry_price
    exit_reason = "time_exit"
    exit_ts = int(future["timestamp"].iloc[-1])
    exit_price = float(future["close"].iloc[-1])
    trail_stop = float("nan")
    ema20_exit_armed = False
    ema20_exit_armed_ts = float("nan")
    ema20_exit_armed_price = float("nan")
    ema20_exit_triggered = False
    ema20_exit_was_better_than_final = False

    for idx, row in future.iterrows():
        candle_ts = int(row["timestamp"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        ema20 = _safe_float(row.get("ema20"))
        max_high = max(max_high, high)
        min_low = min(min_low, low)

        if low <= active_stop:
            exit_reason = "stop_loss" if not tp1_hit else "trailing_stop"
            exit_ts = candle_ts
            exit_price = active_stop
            realized_r += remaining_fraction * ((exit_price - entry_price) / initial_risk)
            remaining_fraction = 0.0
            break

        if (
            config.exit_rule == "ema20_negative_pnl_be_escape"
            and ema20_exit_armed
            and high >= entry_price
        ):
            exit_reason = "ema20_negative_pnl_be_escape"
            exit_ts = candle_ts
            exit_price = entry_price
            realized_r += remaining_fraction * ((exit_price - entry_price) / initial_risk)
            remaining_fraction = 0.0
            ema20_exit_triggered = True
            break

        if not tp1_hit and high >= tp1_price:
            tp1_hit = True
            realized_r += config.tp1_fraction * config.tp1_r
            remaining_fraction = 1.0 - config.tp1_fraction
            if config.move_stop_to_breakeven_after_tp1:
                active_stop = max(active_stop, entry_price)

        if (
            config.exit_rule == "ema20_close"
            and ema20 is not None
            and close < ema20
        ):
            exit_reason = "ema20_close"
            exit_ts = candle_ts
            exit_price = close
            realized_r += remaining_fraction * ((exit_price - entry_price) / initial_risk)
            remaining_fraction = 0.0
            ema20_exit_triggered = True
            break

        if (
            config.exit_rule == "ema20_negative_pnl_be_escape"
            and not ema20_exit_armed
            and ema20 is not None
            and close < ema20
            and close < entry_price
        ):
            ema20_exit_armed = True
            ema20_exit_armed_ts = candle_ts
            ema20_exit_armed_price = close

        if tp1_hit:
            prior = frame.loc[
                (frame["timestamp"] < candle_ts)
                & (frame["timestamp"] >= entry_ts)
            ].tail(config.trail_lookback_candles)
            if not prior.empty:
                structural_stop = float(prior["low"].min()) - config.trail_buffer_r * initial_risk
                if structural_stop > active_stop and structural_stop < close:
                    active_stop = structural_stop
                    trail_stop = active_stop

    if remaining_fraction > 0.0:
        realized_r += remaining_fraction * ((exit_price - entry_price) / initial_risk)
    if np.isfinite(ema20_exit_armed_price):
        ema20_exit_was_better_than_final = ema20_exit_armed_price > exit_price

    gross_return = realized_r * initial_risk / entry_price
    round_trip_fee = config.fee_rate * 2.0
    net_return = gross_return - round_trip_fee
    result = {
        "symbol": symbol,
        "status": "closed",
        "anomaly_timestamp_ms": anomaly_ts,
        "anomaly_timestamp_utc": _timestamp_to_utc(anomaly_ts),
        "decision_timestamp_ms": decision_ts,
        "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
        "entry_timestamp_ms": entry_ts,
        "entry_timestamp_utc": _timestamp_to_utc(entry_ts),
        "entry_method": config.entry_method,
        "execution_model": execution_model,
        "pullback_box_fraction": config.pullback_box_fraction if config.entry_method == "pullback_box_fraction" else np.nan,
        "entry_delay_ms": int(entry_ts - decision_ts),
        "entry_delay_candles": _entry_delay_candles(frame, entry_timestamp_ms=entry_ts, decision_timestamp_ms=decision_ts),
        "entry_price": entry_price,
        "initial_stop": initial_stop,
        "initial_risk": initial_risk,
        "initial_risk_pct": initial_risk_pct,
        "box_range": box_range,
        "box_high": box_high,
        "base_tp1_price": base_tp1_price,
        "tp1_price": tp1_price,
        "tp1_round_step": tp1_round_step,
        "tp1_target_model": "next_round_number_above_1r",
        "tp1_hit": tp1_hit,
        "tp1_fraction": config.tp1_fraction,
        "trail_stop_final": trail_stop,
        "exit_rule": config.exit_rule,
        "ema20_exit_armed": ema20_exit_armed,
        "ema20_exit_armed_timestamp_ms": ema20_exit_armed_ts,
        "ema20_exit_armed_timestamp_utc": _timestamp_to_utc(ema20_exit_armed_ts) if np.isfinite(ema20_exit_armed_ts) else "",
        "ema20_exit_armed_price": ema20_exit_armed_price,
        "ema20_exit_triggered": ema20_exit_triggered,
        "ema20_exit_was_better_than_final": ema20_exit_was_better_than_final,
        "exit_timestamp_ms": exit_ts,
        "exit_timestamp_utc": _timestamp_to_utc(exit_ts),
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "holding_candles": int((future["timestamp"] <= exit_ts).sum()),
        "mfe_pct": (max_high - entry_price) / entry_price,
        "mae_pct": (min_low - entry_price) / entry_price,
        "gross_r": realized_r,
        "gross_return": gross_return,
        "fee_rate": config.fee_rate,
        "net_return": net_return,
        "outcome_label": signal.get("outcome_label", ""),
    }
    for column in TRADE_SIGNAL_CONTEXT_COLUMNS:
        result[column] = signal.get(column, "")
    return result


def simulate_anomaly_trades(
    signals: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    progress_label: str | None = None,
    frame_cache: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    last_exit_by_symbol: dict[str, int] = {}
    if frame_cache is None:
        frame_cache = {}
    ordered_signals = signals.sort_values(["decision_timestamp_ms", "symbol"])
    progress_started_at = time.monotonic()
    next_progress_pct = 0
    total_signals = len(ordered_signals)
    for processed_count, (_, signal) in enumerate(ordered_signals.iterrows(), start=1):
        symbol = str(signal["symbol"])
        entry_ts = int(signal["decision_timestamp_ms"])
        if entry_ts <= last_exit_by_symbol.get(symbol, -1):
            rows.append(
                {
                    "symbol": symbol,
                    "entry_timestamp_ms": entry_ts,
                    "entry_timestamp_utc": _timestamp_to_utc(entry_ts),
                    "status": "skipped",
                    "skip_reason": "overlapping_signal",
                    "entry_method": config.entry_method,
                    "execution_model": _execution_model_label(config),
                }
            )
            continue
        frame = frame_cache.get(symbol)
        if frame is None:
            frame = _read_entry_simulation_frame(
                config.lab_config.cache_dir,
                symbol,
                _effective_entry_timeframe(config),
            )
            frame_cache[symbol] = frame
        result = simulate_long_signal(frame, signal, config=config)
        rows.append(result)
        if result.get("status") == "closed" and "exit_timestamp_ms" in result:
            last_exit_by_symbol[symbol] = int(result["exit_timestamp_ms"])
        if progress_label is not None:
            current_pct = int(100 * processed_count / total_signals)
            if current_pct >= next_progress_pct or processed_count == total_signals:
                _emit_progress(
                    label=progress_label,
                    done=processed_count,
                    total=total_signals,
                    started_at=progress_started_at,
                )
                next_progress_pct = current_pct + 5
    return pd.DataFrame(rows)


def summarize_trade_skip_reasons(trades: pd.DataFrame) -> pd.DataFrame:
    columns = ["status", "skip_reason", "count", "share_of_all", "execution_guard"]
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame(columns=columns)
    skipped = trades.loc[trades["status"].eq("skipped")].copy()
    if skipped.empty:
        return pd.DataFrame(columns=columns)
    if "skip_reason" not in skipped.columns:
        raise ValueError("skipped anomaly trades missing skip_reason column")
    grouped = skipped.groupby("skip_reason", dropna=False).size().reset_index(name="count")
    grouped["status"] = "skipped"
    grouped["share_of_all"] = grouped["count"].astype(float) / float(len(trades))
    grouped["execution_guard"] = grouped["skip_reason"].astype(str).isin(EXECUTION_GUARD_SKIP_REASONS)
    return grouped[columns].sort_values(["count", "skip_reason"], ascending=[False, True]).reset_index(drop=True)


def summarize_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame([{"metric": "closed_trades", "value": 0}, {"metric": "skipped_trades", "value": 0}])
    closed = trades.loc[trades["status"].eq("closed")].copy()
    skipped = trades.loc[trades["status"].eq("skipped")].copy()
    rows: list[dict[str, object]] = [
        {"metric": "closed_trades", "value": int(len(closed))},
        {"metric": "skipped_trades", "value": int(len(skipped))},
    ]
    if not skipped.empty:
        skip_counts = summarize_trade_skip_reasons(trades)
        execution_guard_skips = int(skip_counts.loc[skip_counts["execution_guard"].astype(bool), "count"].sum())
        rows.append({"metric": "execution_guard_skips", "value": execution_guard_skips})
        for _, row in skip_counts.iterrows():
            rows.append({"metric": f"skip_reason:{row['skip_reason']}", "value": int(row["count"])})
    if closed.empty:
        return pd.DataFrame(rows)
    net = closed["net_return"].astype(float)
    gross_r = closed["gross_r"].astype(float)
    wins = net > 0
    rows.extend(
        [
            {"metric": "symbols", "value": int(closed["symbol"].nunique())},
            {"metric": "win_rate", "value": float(wins.mean())},
            {"metric": "avg_net_return", "value": float(net.mean())},
            {"metric": "median_net_return", "value": float(net.median())},
            {"metric": "sum_net_return", "value": float(net.sum())},
            {"metric": "avg_gross_r", "value": float(gross_r.mean())},
            {"metric": "median_gross_r", "value": float(gross_r.median())},
            {"metric": "tp1_hit_rate", "value": float(closed["tp1_hit"].astype(bool).mean())},
            {"metric": "avg_mfe_pct", "value": float(closed["mfe_pct"].astype(float).mean())},
            {"metric": "avg_mae_pct", "value": float(closed["mae_pct"].astype(float).mean())},
        ]
    )
    by_reason = closed.groupby("exit_reason").size().reset_index(name="count")
    for _, row in by_reason.iterrows():
        rows.append({"metric": f"exit_reason:{row['exit_reason']}", "value": int(row["count"])})
    return pd.DataFrame(rows)


def summarize_trades_by_symbol(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame()
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame()
    grouped = (
        closed.assign(win=closed["net_return"].astype(float) > 0)
        .groupby("symbol")
        .agg(
            closed_trades=("symbol", "size"),
            win_rate=("win", "mean"),
            avg_net_return=("net_return", "mean"),
            median_net_return=("net_return", "median"),
            sum_net_return=("net_return", "sum"),
            avg_gross_r=("gross_r", "mean"),
            tp1_hit_rate=("tp1_hit", "mean"),
            avg_mfe_pct=("mfe_pct", "mean"),
            avg_mae_pct=("mae_pct", "mean"),
        )
        .reset_index()
    )
    grouped.sort_values("sum_net_return", ascending=False, inplace=True)
    return grouped


def _parse_grid_values(raw: str, *, cast: type[float] | type[int]) -> list[float] | list[int]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return [cast(item) for item in values]


EXHAUSTION_PROFILES: dict[str, dict[str, float | None]] = {
    "none": {
        "max_start_quote_ratio": None,
        "max_start_trade_ratio": None,
        "max_start_avg_trade_quote_size_ratio": None,
        "max_start_quote_ratio_per_abs_return": None,
        "max_start_range_pct_ratio_to_baseline": None,
        "max_prior_up_down_whipsaw_to_impulse_range": None,
        "min_next_taker_buy_quote_share": None,
        "max_price_retention": None,
    },
    "mild": {
        "max_start_quote_ratio": 120.0,
        "max_start_trade_ratio": 60.0,
        "max_start_avg_trade_quote_size_ratio": 10.0,
        "max_start_quote_ratio_per_abs_return": 30_000.0,
        "max_start_range_pct_ratio_to_baseline": 35.0,
        "max_prior_up_down_whipsaw_to_impulse_range": 0.60,
        "min_next_taker_buy_quote_share": 0.46,
        "max_price_retention": 0.98,
    },
    "balanced": {
        "max_start_quote_ratio": 80.0,
        "max_start_trade_ratio": 40.0,
        "max_start_avg_trade_quote_size_ratio": 7.0,
        "max_start_quote_ratio_per_abs_return": 15_000.0,
        "max_start_range_pct_ratio_to_baseline": 25.0,
        "max_prior_up_down_whipsaw_to_impulse_range": 0.60,
        "min_next_taker_buy_quote_share": 0.48,
        "max_price_retention": 0.96,
    },
    "strict": {
        "max_start_quote_ratio": 50.0,
        "max_start_trade_ratio": 25.0,
        "max_start_avg_trade_quote_size_ratio": 5.0,
        "max_start_quote_ratio_per_abs_return": 8_000.0,
        "max_start_range_pct_ratio_to_baseline": 18.0,
        "max_prior_up_down_whipsaw_to_impulse_range": 0.50,
        "min_next_taker_buy_quote_share": 0.50,
        "max_price_retention": 0.94,
    },
}


def _parse_grid_profile_values(raw: str) -> list[str]:
    profiles = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(profiles).difference(EXHAUSTION_PROFILES))
    if unknown:
        raise ValueError(f"unknown exhaustion profiles: {unknown}")
    return profiles


EXIT_RULES = {"structural_trail", "ema20_close", "ema20_negative_pnl_be_escape"}


def _parse_grid_exit_rules(raw: str) -> list[str]:
    rules = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(rules).difference(EXIT_RULES))
    if unknown:
        raise ValueError(f"unknown exit rules: {unknown}")
    return rules


def _iter_entry_grid_configs(
    base_config: AnomalyBacktestConfig,
    *,
    oi3_values: Iterable[float],
    hold_values: Iterable[int],
    pullback_fractions: Iterable[float],
    exhaustion_profiles: Iterable[str],
    exit_rules: Iterable[str],
) -> list[AnomalyBacktestConfig]:
    variants: list[AnomalyBacktestConfig] = []
    for profile_name in exhaustion_profiles:
        profile = EXHAUSTION_PROFILES[profile_name]
        for oi3 in oi3_values:
            for hold in hold_values:
                for exit_rule in exit_rules:
                    common = {
                        "min_hold_count": int(hold),
                        "min_oi_change_pct_3x5m": float(oi3),
                        "require_oi_status_ok": True,
                        "exhaustion_profile": profile_name,
                        "exit_rule": str(exit_rule),
                        **profile,
                    }
                    variants.append(
                        replace(
                            base_config,
                            entry_method="market",
                            **common,
                        )
                    )
                    variants.append(
                        replace(
                            base_config,
                            entry_method="break_box_high",
                            **common,
                        )
                    )
                    for fraction in pullback_fractions:
                        variants.append(
                            replace(
                                base_config,
                                entry_method="pullback_box_fraction",
                                pullback_box_fraction=float(fraction),
                                **common,
                            )
                        )
    return variants


def _build_entry_grid_signal_sets(
    candidates: pd.DataFrame,
    variants: Iterable[AnomalyBacktestConfig],
) -> list[tuple[AnomalyBacktestConfig, pd.DataFrame]]:
    variant_list = list(variants)
    started_at = time.monotonic()
    next_progress_pct = 0
    signal_sets: list[tuple[AnomalyBacktestConfig, pd.DataFrame]] = []
    for processed_count, variant in enumerate(variant_list, start=1):
        signals = build_anomaly_signals(candidates, config=variant)
        signal_sets.append((variant, signals))
        next_progress_pct = _emit_progress_5pct(
            label="anomaly grid signals",
            done=processed_count,
            total=len(variant_list),
            started_at=started_at,
            next_progress_pct=next_progress_pct,
        )
    return signal_sets


def _signal_universe_from_signal_sets(
    signal_sets: Iterable[tuple[AnomalyBacktestConfig, pd.DataFrame]],
) -> pd.DataFrame:
    signal_frames: list[pd.DataFrame] = []
    for _, signals in signal_sets:
        if not signals.empty:
            signal_frames.append(signals.loc[:, ["symbol", "decision_timestamp_ms"]].copy())
    if not signal_frames:
        return pd.DataFrame(columns=["symbol", "decision_timestamp_ms"])
    universe = pd.concat(signal_frames, ignore_index=True)
    universe.dropna(subset=["symbol", "decision_timestamp_ms"], inplace=True)
    universe.drop_duplicates(["symbol", "decision_timestamp_ms"], inplace=True)
    universe.sort_values(["symbol", "decision_timestamp_ms"], inplace=True)
    universe.reset_index(drop=True, inplace=True)
    return universe


def _fetch_derivatives_context_for_signal_universe(
    signal_universe: pd.DataFrame,
    *,
    derivatives_context_fetcher: object,
) -> pd.DataFrame:
    if signal_universe.empty:
        print("anomaly derivatives context: no post-filter signal universe to fetch", flush=True)
        return pd.DataFrame(
            columns=[
                "symbol",
                "signal_rows",
                "start_timestamp_ms",
                "end_timestamp_ms",
                "success",
                "added_rows",
                "message",
            ]
        )
    fetch_symbol = getattr(derivatives_context_fetcher, "fetch_symbol")
    grouped = signal_universe.groupby("symbol", sort=True)
    started_at = time.monotonic()
    total = int(len(grouped))
    next_progress_pct = 0
    status_rows: list[dict[str, object]] = []
    print(
        "anomaly derivatives context: fetching event windows for "
        f"{len(signal_universe)} post-filter signals across {total} instruments",
        flush=True,
    )
    logger = getattr(derivatives_context_fetcher, "_logger", None)
    previous_disabled = getattr(logger, "disabled", None)
    if logger is not None:
        logger.disabled = True
    try:
        for processed_count, (symbol, group) in enumerate(grouped, start=1):
            timestamps = group["decision_timestamp_ms"].astype(float)
            start_ts = int(timestamps.min()) - 6 * 5 * 60 * 1000
            end_ts = int(timestamps.max()) + 5 * 60 * 1000
            try:
                added_rows = int(fetch_symbol(str(symbol), start_ts, end_ts))
                success = True
                message = "ok"
            except Exception as exc:
                added_rows = 0
                success = False
                message = f"{type(exc).__name__}: {exc}"
            status_rows.append(
                {
                    "symbol": str(symbol),
                    "signal_rows": int(len(group)),
                    "start_timestamp_ms": start_ts,
                    "end_timestamp_ms": end_ts,
                    "success": success,
                    "added_rows": added_rows,
                    "message": message,
                }
            )
            next_progress_pct = _emit_progress_5pct(
                label="anomaly derivatives context",
                done=processed_count,
                total=total,
                started_at=started_at,
                next_progress_pct=next_progress_pct,
            )
    finally:
        if logger is not None and previous_disabled is not None:
            logger.disabled = previous_disabled
    return pd.DataFrame(status_rows)


def _context_default_columns(status: str) -> dict[str, object]:
    defaults: dict[str, object] = {}
    for spec in DERIVATIVES_CONTEXT_SPECS:
        values = _empty_context_columns(spec)
        values[f"{spec['prefix']}_status"] = status
        defaults.update(values)
    defaults["mark_close_vs_decision_close_basis"] = np.nan
    defaults["taker_ls_buy_share"] = np.nan
    return defaults


def _attach_default_context_columns(frame: pd.DataFrame, defaults: dict[str, object]) -> pd.DataFrame:
    default_frame = pd.DataFrame(
        {column: [value] * len(frame) for column, value in defaults.items()},
        index=frame.index,
    )
    return pd.concat([frame.drop(columns=list(defaults), errors="ignore"), default_frame], axis=1)


def _enrich_derivatives_context_for_signal_universe(
    candidates: pd.DataFrame,
    signal_universe: pd.DataFrame,
    *,
    cache_dir: Path,
) -> pd.DataFrame:
    if candidates.empty or "symbol" not in candidates.columns:
        return candidates

    result = candidates.copy()
    defaults = _context_default_columns("not_in_context_universe")
    if signal_universe.empty:
        defaults = _context_default_columns("no_context_universe")
        return _attach_default_context_columns(result, defaults)

    key_columns = ["symbol", "decision_timestamp_ms"]
    if not set(key_columns).issubset(result.columns):
        return _attach_default_context_columns(result, defaults)

    universe_keys = signal_universe.loc[:, key_columns].dropna().drop_duplicates()
    selected_index = (
        result.reset_index()
        .merge(universe_keys, on=key_columns, how="inner")["index"]
        .astype(int)
        .to_numpy()
    )
    result = _attach_default_context_columns(result, defaults)
    if len(selected_index) == 0:
        return result

    selected = result.loc[selected_index].drop(columns=list(defaults), errors="ignore").copy()
    print(f"anomaly derivatives context: enriching {len(selected)} post-filter candidate rows", flush=True)
    enriched = enrich_candidates_with_derivatives_context(
        selected,
        cache_dir=cache_dir,
        progress_label="anomaly derivatives enrich",
    )
    context_columns = [column for column in enriched.columns if column in defaults]
    for column in context_columns:
        result.loc[enriched.index, column] = enriched[column]
    return result


def summarize_entry_grid_variant(
    trades: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    signal_count: int,
    variant_id: int | None = None,
) -> dict[str, object]:
    closed = trades.loc[trades.get("status", pd.Series(dtype=str)).eq("closed")].copy()
    skip_summary = summarize_trade_skip_reasons(trades)
    top_skip_reason = str(skip_summary.iloc[0]["skip_reason"]) if not skip_summary.empty else ""
    execution_guard_skips = int(skip_summary.loc[skip_summary["execution_guard"].astype(bool), "count"].sum()) if not skip_summary.empty else 0
    row: dict[str, object] = {
        "variant_id": int(variant_id) if variant_id is not None else -1,
        "entry_method": config.entry_method,
        "exit_rule": config.exit_rule,
        "pullback_box_fraction": (
            config.pullback_box_fraction if config.entry_method == "pullback_box_fraction" else np.nan
        ),
        "min_hold_count": config.min_hold_count,
        "min_oi_change_pct_3x5m": config.min_oi_change_pct_3x5m,
        "require_oi_status_ok": config.require_oi_status_ok,
        "exhaustion_profile": config.exhaustion_profile,
        "max_start_quote_ratio": config.max_start_quote_ratio,
        "max_start_trade_ratio": config.max_start_trade_ratio,
        "max_start_avg_trade_quote_size_ratio": config.max_start_avg_trade_quote_size_ratio,
        "max_start_quote_ratio_per_abs_return": config.max_start_quote_ratio_per_abs_return,
        "max_start_range_pct_ratio_to_baseline": config.max_start_range_pct_ratio_to_baseline,
        "min_flow_hold_count": config.min_flow_hold_count,
        "max_prior_spike_count_72h": config.max_prior_spike_count_72h,
        "max_prior_fast_fade_count_72h": config.max_prior_fast_fade_count_72h,
        "min_start_lower_wick_to_range": config.min_start_lower_wick_to_range,
        "max_start_upper_wick_to_range": config.max_start_upper_wick_to_range,
        "min_next_taker_buy_quote_share": config.min_next_taker_buy_quote_share,
        "max_price_retention": config.max_price_retention,
        "signals": int(signal_count),
        "closed_trades": int(len(closed)),
        "skipped_trades": int(len(trades) - len(closed)),
        "execution_guard_skips": execution_guard_skips,
        "top_skip_reason": top_skip_reason,
    }
    if closed.empty:
        return row
    net = closed["net_return"].astype(float)
    row.update(
        {
            "symbols": int(closed["symbol"].nunique()),
            "days": int(closed["entry_timestamp_utc"].astype(str).str[:10].nunique()),
            "win_rate": float((net > 0).mean()),
            "avg_net_return": float(net.mean()),
            "median_net_return": float(net.median()),
            "sum_net_return": float(net.sum()),
            "tp1_hit_rate": float(closed["tp1_hit"].astype(bool).mean()),
            "avg_mfe_pct": float(closed["mfe_pct"].astype(float).mean()),
            "avg_mae_pct": float(closed["mae_pct"].astype(float).mean()),
        }
    )
    return row


def build_edge_health_table(trades: pd.DataFrame, *, label: str) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame(
            [
                {
                    "label": label,
                    "aspect": "closed_trades",
                    "value": 0.0,
                    "score_0_100": 0.0,
                    "status": "fail",
                    "note": "No closed trades.",
                }
            ]
        )
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return build_edge_health_table(pd.DataFrame(), label=label)

    net = closed["net_return"].astype(float)
    total = float(net.sum())
    top = net.sort_values(ascending=False)
    daily = closed.assign(day=closed["entry_timestamp_utc"].astype(str).str[:10]).groupby("day")["net_return"].sum()
    positive_days = int((daily > 0).sum())
    active_days = int(len(daily))
    top5_share = float(top.head(5).sum() / total) if total > 0 else float("inf")

    def score_linear(value: float, bad: float, good: float, *, higher_is_better: bool = True) -> float:
        if not np.isfinite(value):
            return 0.0
        if not higher_is_better:
            value = -value
            bad = -bad
            good = -good
        if good == bad:
            return 100.0 if value >= good else 0.0
        return float(np.clip((value - bad) / (good - bad), 0.0, 1.0) * 100.0)

    rows = [
        {
            "label": label,
            "aspect": "closed_trades",
            "value": int(len(closed)),
            "score_0_100": score_linear(float(len(closed)), 0.0, 50.0),
            "status": "ok" if len(closed) >= 30 else "watch" if len(closed) >= 15 else "fail",
            "note": "Monthly frequency target: 15 watch, 30 ok, 50 strong.",
        },
        {
            "label": label,
            "aspect": "win_rate",
            "value": float((net > 0).mean()),
            "score_0_100": score_linear(float((net > 0).mean()), 0.40, 0.70),
            "status": "ok" if (net > 0).mean() >= 0.55 else "watch" if (net > 0).mean() >= 0.40 else "fail",
            "note": "40% is minimum viable, 55% ok, 70% excellent for this style.",
        },
        {
            "label": label,
            "aspect": "avg_net_return",
            "value": float(net.mean()),
            "score_0_100": score_linear(float(net.mean()), 0.0, 0.02),
            "status": "ok" if net.mean() >= 0.01 else "watch" if net.mean() > 0 else "fail",
            "note": "Target average trade is above +1%; +2% is strong.",
        },
        {
            "label": label,
            "aspect": "median_net_return",
            "value": float(net.median()),
            "score_0_100": score_linear(float(net.median()), 0.0, 0.015),
            "status": "ok" if net.median() >= 0.005 else "watch" if net.median() > 0 else "fail",
            "note": "Positive median reduces top-tail dependency risk.",
        },
        {
            "label": label,
            "aspect": "active_days",
            "value": active_days,
            "score_0_100": score_linear(float(active_days), 0.0, 20.0),
            "status": "ok" if active_days >= 15 else "watch" if active_days >= 8 else "fail",
            "note": "More active days means less single-day dependence.",
        },
        {
            "label": label,
            "aspect": "positive_day_share",
            "value": float(positive_days / active_days) if active_days else 0.0,
            "score_0_100": score_linear(float(positive_days / active_days) if active_days else 0.0, 0.45, 0.70),
            "status": "ok" if active_days and positive_days / active_days >= 0.60 else "watch" if active_days and positive_days / active_days >= 0.45 else "fail",
            "note": "Day-level consistency check.",
        },
        {
            "label": label,
            "aspect": "symbol_count",
            "value": int(closed["symbol"].nunique()),
            "score_0_100": score_linear(float(closed["symbol"].nunique()), 1.0, 30.0),
            "status": "ok" if closed["symbol"].nunique() >= 20 else "watch" if closed["symbol"].nunique() >= 10 else "fail",
            "note": "Avoid dependence on one instrument.",
        },
        {
            "label": label,
            "aspect": "top5_dependency",
            "value": top5_share,
            "score_0_100": score_linear(top5_share, 1.0, 0.35, higher_is_better=False),
            "status": "ok" if top5_share <= 0.50 else "watch" if top5_share <= 0.85 else "fail",
            "note": "Lower is better. Above 0.85 means result is dominated by top winners.",
        },
        {
            "label": label,
            "aspect": "worst_day_return",
            "value": float(daily.min()) if active_days else np.nan,
            "score_0_100": score_linear(float(daily.min()) if active_days else float("nan"), -0.20, -0.03),
            "status": "ok" if active_days and daily.min() >= -0.03 else "watch" if active_days and daily.min() >= -0.10 else "fail",
            "note": "Day-level downside containment.",
        },
    ]
    return pd.DataFrame(rows)


def _prepare_anomaly_plot_frame(frame: pd.DataFrame, *, start_ts: int, end_ts: int) -> pd.DataFrame:
    columns = [
        column
        for column in (
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "number_of_trades",
            "taker_buy_volume",
            "taker_buy_quote_volume",
        )
        if column in frame.columns
    ]
    plot_frame = frame.loc[(frame["timestamp"] >= start_ts) & (frame["timestamp"] <= end_ts), columns].copy()
    if plot_frame.empty:
        return plot_frame
    for column in columns:
        plot_frame[column] = pd.to_numeric(plot_frame[column], errors="coerce")
    plot_frame = plot_frame.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    if plot_frame.empty:
        return plot_frame
    plot_frame.sort_values("timestamp", inplace=True, kind="stable")
    plot_frame.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)
    plot_frame["ema9"] = plot_frame["close"].ewm(span=9, adjust=False).mean()
    plot_frame["ema20"] = plot_frame["close"].ewm(span=20, adjust=False).mean()
    return plot_frame.reset_index(drop=True)


def _draw_anomaly_trade_block(
    ax,
    *,
    start_idx: int | None,
    end_idx: int | None,
    lower_price: float | None,
    upper_price: float | None,
    facecolor: str,
    edgecolor: str,
    alpha: float,
    zorder: float,
    frame_length: int,
) -> None:
    if start_idx is None or end_idx is None or lower_price is None or upper_price is None or frame_length <= 0:
        return
    block_start = min(max(int(start_idx), 0), frame_length - 1)
    block_end = min(max(int(end_idx), block_start), frame_length - 1)
    price_low = min(float(lower_price), float(upper_price))
    price_high = max(float(lower_price), float(upper_price))
    from matplotlib.patches import Rectangle

    ax.add_patch(
        Rectangle(
            (block_start - 0.5, price_low),
            max(float(block_end - block_start + 1), 1.0),
            max(price_high - price_low, 1e-9),
            facecolor=facecolor,
            edgecolor=edgecolor,
            linewidth=0.9,
            alpha=alpha,
            zorder=zorder,
        )
    )


def _annotate_anomaly_panel_message(ax, text: str) -> None:
    ax.text(
        0.5,
        0.5,
        text,
        transform=ax.transAxes,
        ha="center",
        va="center",
        color=CHART_MUTED,
        fontsize=7,
        alpha=0.72,
    )


def _normalize_chart_percent(frame: pd.DataFrame, column: str) -> np.ndarray | None:
    if column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    if not values.notna().any():
        return None
    array = values.fillna(0.0).to_numpy(dtype=np.float64, copy=False)
    max_value = float(np.nanmax(array)) if array.size else 0.0
    if max_value <= 0.0:
        return None
    return (array / max_value) * 100.0


def _build_trade_chart_hourly_context(frame: pd.DataFrame, *, end_timestamp_ms: int, days: int) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close"}
    if frame.empty or required.difference(frame.columns):
        return pd.DataFrame()

    end_exclusive = ((int(end_timestamp_ms) // _HOUR_MS) + 1) * _HOUR_MS
    start_inclusive = end_exclusive - max(int(days), 1) * _DAY_MS
    source = frame.copy()
    source["timestamp"] = pd.to_numeric(source["timestamp"], errors="coerce")
    source = source.dropna(subset=["timestamp", "open", "high", "low", "close"])
    source["timestamp"] = source["timestamp"].astype("int64")
    source = source.loc[source["timestamp"].ge(start_inclusive) & source["timestamp"].lt(end_exclusive)].copy()
    if source.empty:
        return pd.DataFrame()

    for column in ("open", "high", "low", "close", "volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in source.columns:
            source[column] = pd.to_numeric(source[column], errors="coerce")
    source = source.dropna(subset=["open", "high", "low", "close"])
    source = source.loc[(source["open"] > 0.0) & (source["high"] > 0.0) & (source["low"] > 0.0) & (source["close"] > 0.0)].copy()
    if source.empty:
        return pd.DataFrame()

    source.sort_values("timestamp", inplace=True)
    source.drop_duplicates("timestamp", keep="last", inplace=True)
    source.index = pd.to_datetime(source["timestamp"], unit="ms", utc=True)
    aggregation: dict[str, str] = {
        "timestamp": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in source.columns:
            aggregation[column] = "sum"
    hourly = source.resample("1h", label="left", closed="left").agg(aggregation)
    hourly = hourly.dropna(subset=["open", "high", "low", "close"]).copy()
    if hourly.empty:
        return pd.DataFrame()
    hourly["timestamp"] = (hourly.index.view("int64") // 1_000_000).astype("int64")
    hourly.reset_index(drop=True, inplace=True)
    return hourly


def _slice_trade_chart_hourly_level_context(
    hourly_context: pd.DataFrame,
    *,
    end_timestamp_ms: int,
    days: int,
) -> pd.DataFrame:
    """Return the exact 1h context window used for chart-level discovery."""
    if hourly_context.empty or "timestamp" not in hourly_context.columns:
        return pd.DataFrame()

    end_exclusive = ((int(end_timestamp_ms) // _HOUR_MS) + 1) * _HOUR_MS
    start_inclusive = end_exclusive - max(int(days), 1) * _DAY_MS
    sliced = hourly_context.copy()
    sliced["timestamp"] = pd.to_numeric(sliced["timestamp"], errors="coerce")
    sliced = sliced.dropna(subset=["timestamp"])
    if sliced.empty:
        return pd.DataFrame()
    sliced["timestamp"] = sliced["timestamp"].astype("int64")
    sliced = sliced.loc[
        sliced["timestamp"].ge(start_inclusive)
        & sliced["timestamp"].lt(end_exclusive)
    ].copy()
    if sliced.empty:
        return pd.DataFrame()
    sliced.sort_values("timestamp", inplace=True)
    sliced.drop_duplicates("timestamp", keep="last", inplace=True)
    sliced.reset_index(drop=True, inplace=True)
    return sliced


def _find_trade_chart_hourly_levels(
    hourly_context: pd.DataFrame,
    *,
    symbol: str,
    end_timestamp_ms: int,
    days: int = _TRADE_CHART_CONTEXT_DAYS,
) -> list[object]:
    level_context = _slice_trade_chart_hourly_level_context(
        hourly_context,
        end_timestamp_ms=end_timestamp_ms,
        days=days,
    )
    if level_context.empty:
        return []

    from research_tools.hourly_levels import HourlyLevelScanConfig, find_hourly_overhead_levels

    config = HourlyLevelScanConfig(
        cache_dir=Path(),
        output_dir=Path(),
        symbols=(symbol,),
        days=days,
        lookback_bars=days * 24,
        chart_bars=days * 24,
    )
    levels, _trend, _reason = find_hourly_overhead_levels(
        level_context,
        symbol=symbol,
        config=config,
    )
    return list(levels)


def _build_trade_chart_timeframe_context(
    frame: pd.DataFrame,
    *,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
    timeframe_ms: int | None,
) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close"}
    if frame.empty or required.difference(frame.columns):
        return pd.DataFrame()
    if timeframe_ms is None or int(timeframe_ms) <= 0:
        return pd.DataFrame()

    source = frame.copy()
    source["timestamp"] = pd.to_numeric(source["timestamp"], errors="coerce")
    source = source.dropna(subset=["timestamp", "open", "high", "low", "close"])
    source["timestamp"] = source["timestamp"].astype("int64")
    source = source.loc[
        source["timestamp"].ge(int(start_timestamp_ms))
        & source["timestamp"].le(int(end_timestamp_ms))
    ].copy()
    if source.empty:
        return pd.DataFrame()
    for column in ("open", "high", "low", "close", "volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in source.columns:
            source[column] = pd.to_numeric(source[column], errors="coerce")
    source = source.dropna(subset=["open", "high", "low", "close"])
    if source.empty:
        return pd.DataFrame()
    source.sort_values("timestamp", inplace=True)
    source.drop_duplicates("timestamp", keep="last", inplace=True)
    source["bucket"] = (source["timestamp"] // int(timeframe_ms)) * int(timeframe_ms)
    aggregation: dict[str, str] = {
        "timestamp": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    for column in ("volume", "quote_volume", "number_of_trades", "taker_buy_quote_volume"):
        if column in source.columns:
            aggregation[column] = "sum"
    grouped = source.groupby("bucket", as_index=False).agg(aggregation)
    grouped["timestamp"] = grouped["bucket"].astype("int64")
    grouped.drop(columns=["bucket"], inplace=True)
    grouped.sort_values("timestamp", inplace=True)
    grouped.reset_index(drop=True, inplace=True)
    return grouped


def _format_ru_timeframe_label(step_ms: int | None) -> str:
    if step_ms is None or step_ms <= 0:
        return "Цена"
    seconds = step_ms / 1000.0
    if seconds < 60:
        return f"{seconds:g}с"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:g}м"
    hours = minutes / 60.0
    return f"{hours:g}ч"


def _annotate_chart_panel_label(ax, text: str) -> None:
    ax.text(
        0.02,
        0.96,
        text,
        transform=ax.transAxes,
        ha="left",
        va="top",
        color="#ffffff",
        fontsize=18,
        fontweight="bold",
        alpha=0.25,
        zorder=0.2,
    )


def _apply_trade_chart_ticks(ax, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    tick_timestamps = build_tick_timestamps(frame)
    tick_positions = build_tick_positions_from_timestamps(tick_timestamps, len(frame))
    tick_labels = build_tick_labels_from_timestamps(tick_timestamps, tick_positions)
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels)


def _render_anomaly_trade_chart(
    *,
    frame: pd.DataFrame,
    trade: pd.Series,
    output_path: Path,
    pre_candles: int = 30,
    post_candles: int = 90,
    context_timeframe_ms: int | None = 300_000,
    hourly_context_frame: pd.DataFrame | None = None,
    draw_risk_reward_blocks: bool = True,
    draw_exit_marker: bool | None = None,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    anomaly_ts = _safe_int(trade.get("anomaly_timestamp_ms")) or _safe_int(trade.get("decision_timestamp_ms"))
    decision_ts = _safe_int(trade.get("decision_timestamp_ms")) or anomaly_ts
    entry_ts = _safe_int(trade.get("entry_timestamp_ms")) or decision_ts
    raw_exit_ts = _safe_int(trade.get("exit_timestamp_ms"))
    exit_ts = raw_exit_ts or entry_ts
    if anomaly_ts is None or decision_ts is None or entry_ts is None or exit_ts is None:
        raise ValueError("missing_trade_timestamps")
    if draw_exit_marker is None:
        status = str(trade.get("status", "")).lower()
        draw_exit_marker = raw_exit_ts is not None and raw_exit_ts != entry_ts and status != "open"
    entry_price = _safe_float(trade.get("entry_price"))
    signal_entry_price = _safe_float(trade.get("signal_entry_price"))
    signal_entry_ts = _safe_int(trade.get("signal_entry_timestamp_ms")) or decision_ts
    initial_stop = _safe_float(trade.get("initial_stop"))
    tp1_price = _safe_float(trade.get("tp1_price"))
    box_high = _safe_float(trade.get("box_high"))
    exit_price = _safe_float(trade.get("exit_price"))
    timeframe_ms = int(infer_frame_step_ms(frame) or 60_000)
    start_ts = anomaly_ts - pre_candles * timeframe_ms
    end_ts = exit_ts + post_candles * timeframe_ms
    plot_frame = _prepare_anomaly_plot_frame(frame, start_ts=start_ts, end_ts=end_ts)
    if plot_frame.empty:
        raise ValueError("empty_plot_window")
    x_values = np.arange(len(plot_frame), dtype=np.float64)
    timestamps = plot_frame["timestamp"].to_numpy(dtype=np.int64)
    htf_frame = _build_trade_chart_timeframe_context(
        frame,
        start_timestamp_ms=start_ts,
        end_timestamp_ms=end_ts,
        timeframe_ms=context_timeframe_ms,
    )
    htf_x_values = np.arange(len(htf_frame), dtype=np.float64)
    context_source_frame = hourly_context_frame if hourly_context_frame is not None else frame
    context_frame = _build_trade_chart_hourly_context(
        context_source_frame,
        end_timestamp_ms=end_ts,
        days=_TRADE_CHART_CONTEXT_DAYS,
    )
    context_x_values = np.arange(len(context_frame), dtype=np.float64)
    context_levels = _find_trade_chart_hourly_levels(
        context_frame,
        symbol=str(trade.get("symbol", "")),
        end_timestamp_ms=end_ts,
        days=_TRADE_CHART_CONTEXT_DAYS,
    )
    anomaly_idx = resolve_timestamp_plot_idx(timestamps, anomaly_ts)
    decision_idx = resolve_timestamp_plot_idx(timestamps, decision_ts)
    entry_idx = resolve_timestamp_plot_idx(timestamps, entry_ts)
    signal_entry_idx = resolve_timestamp_plot_idx(timestamps, signal_entry_ts)
    exit_idx = resolve_timestamp_plot_idx(timestamps, exit_ts)
    price_axis_right_x = float(len(plot_frame) - 0.5)

    fig, (ax_price, ax_htf, ax_flow, ax_context) = plt.subplots(
        4,
        1,
        figsize=TRADE_CHART_FIGSIZE,
        sharex=False,
        gridspec_kw={"height_ratios": [2.0, 2.0, 1.0, 1.0], "hspace": 0.08},
        facecolor=CHART_FIGURE_FACE,
    )
    configure_plot_axes(price_ax=ax_price, volume_ax=ax_htf, trades_ax=ax_flow)
    configure_plot_axes(price_ax=ax_context, volume_ax=ax_context)

    draw_candles(ax_price, plot_frame, x_values)
    if not htf_frame.empty:
        draw_candles(ax_htf, htf_frame, htf_x_values, alpha=0.94, zorder=3.0)
    else:
        _annotate_anomaly_panel_message(ax_htf, "HTF context unavailable")
    if not context_frame.empty:
        draw_candles(ax_context, context_frame, context_x_values, alpha=0.92, zorder=3.0)
        for level in context_levels:
            level_price = _safe_float(getattr(level, "level_price", None))
            if level_price is not None:
                ax_context.axhline(
                    level_price,
                    color=CHART_LEVEL,
                    linewidth=0.85,
                    alpha=0.62,
                    linestyle="--",
                    zorder=4.5,
                )
    else:
        _annotate_anomaly_panel_message(ax_context, "1h / 4d context unavailable")
    ax_price.plot(x_values, plot_frame["ema9"].to_numpy(dtype=np.float64), color=CHART_EMA9, linewidth=1.2, alpha=0.28, zorder=2.2)
    ax_price.plot(x_values, plot_frame["ema20"].to_numpy(dtype=np.float64), color=CHART_EMA20, linewidth=1.2, alpha=0.24, zorder=2.1)

    ax_price.axvline(anomaly_idx, color=CHART_ANOMALY, linewidth=0.95, alpha=0.30, zorder=5)
    ax_price.axvline(decision_idx, color=CHART_ENTRY, linewidth=0.9, alpha=0.18, linestyle="--", zorder=4.8)
    if signal_entry_price is not None and signal_entry_ts != entry_ts:
        ax_price.axvline(signal_entry_idx, color=CHART_MUTED, linewidth=0.9, alpha=0.30, linestyle=":", zorder=5.0)
    ax_price.axvline(entry_idx, color=CHART_ENTRY, linewidth=1.0, alpha=0.45, zorder=5.2)
    if draw_exit_marker:
        ax_price.axvline(exit_idx, color=CHART_EXIT, linewidth=1.0, alpha=0.52, linestyle="-.", zorder=5.3)

    draw_price_zone(
        ax_price,
        timestamps=timestamps,
        start_timestamp_ms=anomaly_ts,
        end_timestamp_ms=decision_ts,
        low=initial_stop,
        high=box_high,
        facecolor="#1d4ed8",
        edgecolor="#60a5fa",
        alpha=0.10,
        linewidth=0.9,
        zorder=2.0,
    )
    if draw_risk_reward_blocks:
        _draw_anomaly_trade_block(
            ax_price,
            start_idx=entry_idx,
            end_idx=exit_idx,
            lower_price=initial_stop,
            upper_price=entry_price,
            facecolor=CHART_RISK_FACE,
            edgecolor=CHART_RISK_EDGE,
            alpha=0.30,
            zorder=1.05,
            frame_length=len(plot_frame),
        )
        _draw_anomaly_trade_block(
            ax_price,
            start_idx=entry_idx,
            end_idx=exit_idx,
            lower_price=entry_price,
            upper_price=tp1_price,
            facecolor=CHART_PROFIT_FACE,
            edgecolor=CHART_PROFIT_EDGE,
            alpha=0.22,
            zorder=1.08,
            frame_length=len(plot_frame),
        )
    else:
        if tp1_price is not None:
            ax_price.axhline(tp1_price, color=CHART_PROFIT_EDGE, linewidth=1.1, alpha=0.78, linestyle="--", zorder=4.35)
        if initial_stop is not None:
            ax_price.axhline(initial_stop, color=CHART_RISK_EDGE, linewidth=1.1, alpha=0.78, linestyle="--", zorder=4.35)

    tag_input = [
        ("TP1", tp1_price),
        ("Entry", entry_price),
        ("SL", initial_stop),
    ]
    if draw_exit_marker:
        tag_input.append(("Exit", exit_price))
    if signal_entry_price is not None and signal_entry_ts != entry_ts:
        tag_input.append(("Signal", signal_entry_price))
    tag_positions = resolve_axis_tag_positions(tag_input)
    tag_specs = [
        ("TP1", tp1_price, CHART_PROFIT_EDGE, entry_idx - 0.5),
        ("Entry", entry_price, CHART_ENTRY, entry_idx),
        ("SL", initial_stop, CHART_RISK_EDGE, entry_idx - 0.5),
    ]
    if draw_exit_marker:
        tag_specs.append(("Exit", exit_price, CHART_EXIT, exit_idx))
    if signal_entry_price is not None and signal_entry_ts != entry_ts:
        tag_specs.append(("Signal", signal_entry_price, CHART_MUTED, signal_entry_idx))
    for label, value, color, leader_x in tag_specs:
        annotate_axis_price_tag(
            ax_price,
            y=value,
            label=label,
            color=color,
            leader_start_x=float(leader_x),
            leader_end_x=price_axis_right_x,
            text_y=tag_positions.get(label),
            alpha=0.92,
        )

    volume_column = "quote_volume" if "quote_volume" in plot_frame.columns else "volume"
    volume_pct = _normalize_chart_percent(plot_frame, volume_column)
    trade_count_pct = _normalize_chart_percent(plot_frame, "number_of_trades")
    if volume_pct is not None:
        ax_flow.plot(
            x_values,
            volume_pct,
            color=CHART_DOWN,
            linewidth=1.15,
            alpha=0.72,
            zorder=3.0,
        )
    if trade_count_pct is not None:
        ax_flow.plot(
            x_values,
            trade_count_pct,
            color=CHART_UP,
            linewidth=1.25,
            alpha=0.88,
            zorder=4.0,
        )
    if volume_pct is None and trade_count_pct is None:
        _annotate_anomaly_panel_message(ax_flow, "volume / number_of_trades missing")

    high_values = plot_frame["high"].to_numpy(dtype=np.float64)
    low_values = plot_frame["low"].to_numpy(dtype=np.float64)
    price_ylim_values = [
        *high_values.tolist(),
        *low_values.tolist(),
        entry_price,
        initial_stop,
        tp1_price,
    ]
    if draw_exit_marker:
        price_ylim_values.append(exit_price)
    finite_price_ylim_values = [float(value) for value in price_ylim_values if value is not None and math.isfinite(float(value))]
    if not finite_price_ylim_values:
        raise ValueError("empty_price_axis_values")
    padding = max((max(finite_price_ylim_values) - min(finite_price_ylim_values)) * 0.05, 1e-9)
    ax_price.set_ylim(min(finite_price_ylim_values) - padding, max(finite_price_ylim_values) + padding)
    if not context_frame.empty:
        context_high = context_frame["high"].to_numpy(dtype=np.float64)
        context_low = context_frame["low"].to_numpy(dtype=np.float64)
        context_padding = max((float(np.nanmax(context_high)) - float(np.nanmin(context_low))) * 0.08, 1e-9)
        ax_context.set_ylim(float(np.nanmin(context_low)) - context_padding, float(np.nanmax(context_high)) + context_padding)
    if not htf_frame.empty:
        htf_high = htf_frame["high"].to_numpy(dtype=np.float64)
        htf_low = htf_frame["low"].to_numpy(dtype=np.float64)
        htf_padding = max((float(np.nanmax(htf_high)) - float(np.nanmin(htf_low))) * 0.06, 1e-9)
        ax_htf.set_ylim(float(np.nanmin(htf_low)) - htf_padding, float(np.nanmax(htf_high)) + htf_padding)
    entry_candle_width = resolve_candle_width(x_values)
    ax_price.set_xlim(-max(0.5, entry_candle_width * 0.65), len(plot_frame) - 1 + max(0.5, entry_candle_width * 0.65))
    if not context_frame.empty:
        ax_context.set_xlim(-0.5, len(context_frame) - 0.5)
    if not htf_frame.empty:
        htf_candle_width = resolve_candle_width(htf_x_values)
        ax_htf.set_xlim(-max(0.5, htf_candle_width * 0.65), len(htf_frame) - 1 + max(0.5, htf_candle_width * 0.65))
    ax_flow.set_ylim(0.0, 100.0)
    ax_flow.set_yticks([0.0, 50.0, 100.0])
    ax_flow.set_yticklabels(["0", "50", "100"], color=CHART_MUTED)
    ax_price.set_ylabel(_format_ru_timeframe_label(infer_frame_step_ms(plot_frame)))
    ax_htf.set_ylabel(_format_ru_timeframe_label(context_timeframe_ms))
    ax_flow.set_ylabel("Объем / сделки %")
    ax_context.set_ylabel("1ч")
    _annotate_chart_panel_label(ax_price, _format_ru_timeframe_label(infer_frame_step_ms(plot_frame)))
    _annotate_chart_panel_label(ax_htf, _format_ru_timeframe_label(context_timeframe_ms))
    _annotate_chart_panel_label(ax_flow, "Объем / сделки")
    _annotate_chart_panel_label(ax_context, "1ч")
    ax_price.set_title(
        (
            f"{format_chart_symbol(str(trade.get('symbol')))}  "
            f"{trade.get('entry_timestamp_utc')}  "
            f"net={float(trade.get('net_return', 0.0)):.2%}  "
            f"{trade.get('exit_reason', '')}"
        ),
        loc="left",
        color=CHART_TEXT,
        fontsize=10,
        pad=10,
        fontweight="semibold",
    )

    _apply_trade_chart_ticks(ax_htf, htf_frame)
    _apply_trade_chart_ticks(ax_context, context_frame)
    _apply_trade_chart_ticks(ax_flow, plot_frame)
    ax_price.tick_params(axis="x", labelbottom=False)
    ax_htf.tick_params(axis="x", labelbottom=True)
    ax_context.tick_params(axis="x", labelbottom=True)
    ax_flow.tick_params(axis="x", labelbottom=True)
    ax_price.margins(x=0.01)
    ax_htf.margins(x=0.01)
    ax_context.margins(x=0.0)
    ax_flow.margins(x=0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(left=0.10, right=0.80, top=0.94, bottom=0.06, hspace=0.05)
    fig.savefig(output_path, **CHART_SAVEFIG_KWARGS)
    plt.close(fig)


def render_anomaly_trade_chart(
    *,
    frame: pd.DataFrame,
    trade: pd.Series | dict[str, object],
    output_path: Path,
    pre_candles: int = 30,
    post_candles: int = 90,
    context_timeframe_ms: int | None = 300_000,
    hourly_context_frame: pd.DataFrame | None = None,
    draw_risk_reward_blocks: bool = True,
    draw_exit_marker: bool | None = None,
) -> None:
    """Render one anomaly trade chart through the canonical backtest chart renderer."""

    trade_series = trade if isinstance(trade, pd.Series) else pd.Series(trade)
    _render_anomaly_trade_chart(
        frame=frame,
        trade=trade_series,
        output_path=output_path,
        pre_candles=pre_candles,
        post_candles=post_candles,
        context_timeframe_ms=context_timeframe_ms,
        hourly_context_frame=hourly_context_frame,
        draw_risk_reward_blocks=draw_risk_reward_blocks,
        draw_exit_marker=draw_exit_marker,
    )


def render_anomaly_trade_charts(
    trades: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
    output_dir: Path,
    max_charts: int = 80,
) -> pd.DataFrame:
    columns = ["symbol", "entry_timestamp_ms", "status", "chart_status", "chart_reason", "chart_path"]
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame(columns=columns)
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame(columns=columns)
    closed["_abs_net"] = closed["net_return"].astype(float).abs()
    selected = closed.sort_values(["_abs_net", "entry_timestamp_ms"], ascending=[False, True]).head(max_charts).copy()
    selected.sort_values("entry_timestamp_ms", inplace=True)
    frame_cache: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    started_at = time.monotonic()
    next_progress_pct = 0
    for processed_count, (_, trade) in enumerate(selected.iterrows(), start=1):
        symbol = str(trade["symbol"])
        entry_ts = int(trade["entry_timestamp_ms"])
        file_name = f"{processed_count:04d}_{_sanitize_file_part(symbol)}_{entry_ts}.png"
        chart_path = output_dir / file_name
        try:
            frame = frame_cache.get(symbol)
            if frame is None:
                frame = _read_symbol_frame(config.lab_config.cache_dir, symbol, _effective_entry_timeframe(config))
                frame_cache[symbol] = frame
            render_anomaly_trade_chart(frame=frame, trade=trade, output_path=chart_path)
            chart_status = "rendered"
            chart_reason = "ok"
        except Exception as exc:
            chart_status = "not_rendered"
            chart_reason = f"{type(exc).__name__}: {exc}"
            chart_path = Path("")
        rows.append(
            {
                "symbol": symbol,
                "entry_timestamp_ms": entry_ts,
                "status": str(trade.get("status", "")),
                "chart_status": chart_status,
                "chart_reason": chart_reason,
                "chart_path": str(chart_path),
            }
        )
        next_progress_pct = _emit_progress_5pct(
            label="anomaly charts",
            done=processed_count,
            total=len(selected),
            started_at=started_at,
            next_progress_pct=next_progress_pct,
        )
    return pd.DataFrame(rows, columns=columns)



def run_anomaly_entry_grid(
    candidates: pd.DataFrame,
    base_config: AnomalyBacktestConfig,
    *,
    oi3_values: Iterable[float],
    hold_values: Iterable[int],
    pullback_fractions: Iterable[float],
    exhaustion_profiles: Iterable[str],
    exit_rules: Iterable[str],
    signal_sets: list[tuple[AnomalyBacktestConfig, pd.DataFrame]] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if signal_sets is None:
        variants = _iter_entry_grid_configs(
            base_config,
            oi3_values=oi3_values,
            hold_values=hold_values,
            pullback_fractions=pullback_fractions,
            exhaustion_profiles=exhaustion_profiles,
            exit_rules=exit_rules,
        )
        signal_sets = _build_entry_grid_signal_sets(candidates, variants)
    started_at = time.monotonic()
    next_progress_pct = 0
    frame_cache: dict[str, pd.DataFrame] = {}
    for idx, (variant, signals) in enumerate(signal_sets, start=1):
        trades = simulate_anomaly_trades(signals, config=variant, frame_cache=frame_cache)
        rows.append(summarize_entry_grid_variant(trades, config=variant, signal_count=len(signals), variant_id=idx - 1))
        next_progress_pct = _emit_progress_5pct(
            label="anomaly entry grid",
            done=idx,
            total=len(signal_sets),
            started_at=started_at,
            next_progress_pct=next_progress_pct,
        )
    result = pd.DataFrame(rows)
    if not result.empty and "avg_net_return" in result.columns:
        result.sort_values(["avg_net_return", "closed_trades"], ascending=[False, False], inplace=True)
    return result


def run_anomaly_strategy_backtest(
    config: AnomalyBacktestConfig,
    *,
    symbols: Iterable[str] | None = None,
    precollected_candidates: pd.DataFrame | None = None,
    run_entry_grid: bool = False,
    grid_oi3_values: Iterable[float] = (0.01, 0.02, 0.03),
    grid_hold_values: Iterable[int] = (1, 2),
    grid_pullback_fractions: Iterable[float] = (0.65, 0.75, 0.85),
    grid_exhaustion_profiles: Iterable[str] = ("none",),
    grid_exit_rules: Iterable[str] = ("structural_trail",),
    derivatives_context_fetcher: object | None = None,
    render_charts: bool = True,
) -> Path:
    config = _apply_red_flag_profile(config)
    output_dir = config.lab_config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    if precollected_candidates is not None:
        candidates = precollected_candidates.copy()
    elif _effective_entry_timeframe(config) != _effective_setup_timeframe(config):
        candidates = collect_pair_anomaly_rows(
            config,
            symbols=symbols,
            progress_label="anomaly candidates",
            include_derivatives_context=derivatives_context_fetcher is None,
        )
    else:
        candidates = collect_anomaly_lab_rows(
            config.lab_config,
            symbols=symbols,
            progress_label="anomaly candidates",
            include_derivatives_context=derivatives_context_fetcher is None,
        )
        if not candidates.empty:
            candidates = candidates.copy()
            candidates["feature_contract"] = "closed_setup_tf_v1"
            candidates["setup_timeframe"] = _effective_setup_timeframe(config)
            candidates["entry_timeframe"] = _effective_entry_timeframe(config)
            candidates["setup_source"] = "closed_setup_tf"
            candidates["setup_elapsed_fraction"] = 1.0
            candidates["setup_closed_entry_candles"] = candidates.get("confirmation_candles", config.lab_config.confirmation_candles)
    print("anomaly signals: filtering", flush=True)
    pre_context_config = replace(
        config,
        red_flag_profile="none",
        min_mark_close_vs_decision_close_basis=None,
        reject_oi_down_mark_discount=False,
        reject_stale_derivatives_context=False,
    )
    signals = build_anomaly_signals(
        candidates,
        config=pre_context_config if derivatives_context_fetcher is not None else config,
    )
    grid_signal_sets: list[tuple[AnomalyBacktestConfig, pd.DataFrame]] | None = None
    if derivatives_context_fetcher is not None:
        if run_entry_grid:
            grid_variants = _iter_entry_grid_configs(
                config,
                oi3_values=grid_oi3_values,
                hold_values=grid_hold_values,
                pullback_fractions=grid_pullback_fractions,
                exhaustion_profiles=grid_exhaustion_profiles,
                exit_rules=grid_exit_rules,
            )
            grid_signal_sets = _build_entry_grid_signal_sets(candidates, grid_variants)
            context_signals = _signal_universe_from_signal_sets(grid_signal_sets)
        else:
            if {"symbol", "decision_timestamp_ms"}.issubset(signals.columns):
                context_signals = signals.loc[:, ["symbol", "decision_timestamp_ms"]].copy()
            else:
                context_signals = pd.DataFrame(columns=["symbol", "decision_timestamp_ms"])
        context_fetch_status = _fetch_derivatives_context_for_signal_universe(
            context_signals,
            derivatives_context_fetcher=derivatives_context_fetcher,
        )
        _write_artifact_frames(
            [(output_dir / "market_context_fetch_status.csv", context_fetch_status)],
            progress_label="anomaly artifacts: context fetch status",
        )
        candidates = _enrich_derivatives_context_for_signal_universe(
            candidates,
            context_signals,
            cache_dir=config.lab_config.cache_dir,
        )
        red_flag_universe = build_anomaly_signals(candidates, config=pre_context_config)
        signals = build_anomaly_signals(candidates, config=config)
    else:
        red_flag_universe = build_anomaly_signals(candidates, config=pre_context_config)
    end_ms = config.lab_config.end_timestamp_ms
    if end_ms is None:
        end_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    start_ms = int((datetime.fromtimestamp(int(end_ms) / 1000, UTC) - pd.Timedelta(days=config.lab_config.days)).timestamp() * 1000)
    _write_artifact_frames(
        [
            (output_dir / "anomaly_candidates.csv", candidates),
            (
                output_dir / "entry_cache_coverage.csv",
                build_entry_cache_coverage(
                    cache_dir=config.lab_config.cache_dir,
                    setup_timeframe=_effective_setup_timeframe(config),
                    entry_timeframe=_effective_entry_timeframe(config),
                    start_ms=start_ms,
                    end_ms=int(end_ms),
                    symbols=symbols,
                ),
            ),
            (output_dir / "anomaly_red_flag_summary.csv", build_red_flag_summary(red_flag_universe, config=config)),
            (output_dir / "oi_context_status.csv", build_oi_context_status(candidates)),
            (output_dir / "market_context_status.csv", build_derivatives_context_status(candidates)),
            (output_dir / "anomaly_signals.csv", signals),
        ],
        progress_label="anomaly artifacts: base files",
    )
    print(f"anomaly trades: simulating {len(signals)} signals", flush=True)
    trades = simulate_anomaly_trades(signals, config=config, progress_label="anomaly trades")
    summary = summarize_trades(trades)
    skip_reasons = summarize_trade_skip_reasons(trades)
    _write_artifact_frames(
        [
            (output_dir / "anomaly_trades.csv", trades),
            (output_dir / "anomaly_profitability_summary.csv", summary),
            (output_dir / "anomaly_skip_reasons.csv", skip_reasons),
            (output_dir / "anomaly_profitability_by_symbol.csv", summarize_trades_by_symbol(trades)),
        ],
        progress_label="anomaly artifacts: trade files",
    )
    if run_entry_grid:
        print("anomaly entry grid: running variants", flush=True)
        grid = run_anomaly_entry_grid(
            candidates,
            config,
            oi3_values=grid_oi3_values,
            hold_values=grid_hold_values,
            pullback_fractions=grid_pullback_fractions,
            exhaustion_profiles=grid_exhaustion_profiles,
            exit_rules=grid_exit_rules,
            signal_sets=grid_signal_sets,
        )
        _write_artifact_frames(
            [(output_dir / "anomaly_entry_grid_summary.csv", grid)],
            progress_label="anomaly artifacts: grid files",
        )
        if grid_signal_sets is not None and not grid.empty and "variant_id" in grid.columns:
            best_variant_id = int(grid.iloc[0]["variant_id"])
            if 0 <= best_variant_id < len(grid_signal_sets):
                best_config, best_signals = grid_signal_sets[best_variant_id]
                best_trades = simulate_anomaly_trades(best_signals, config=best_config)
                best_label = (
                    f"grid_best_{best_config.exhaustion_profile}_"
                    f"oi{best_config.min_oi_change_pct_3x5m}_hold{best_config.min_hold_count}_"
                    f"{best_config.entry_method}_{best_config.exit_rule}"
                )
                best_config_frame = pd.DataFrame([{**asdict(best_config), "lab_config": asdict(best_config.lab_config)}])
                best_health = build_edge_health_table(best_trades, label=best_label)
                best_skip_reasons = summarize_trade_skip_reasons(best_trades)
                _write_artifact_frames(
                    [
                        (output_dir / "anomaly_entry_grid_best_trades.csv", best_trades),
                        (output_dir / "anomaly_entry_grid_best_config.csv", best_config_frame),
                        (output_dir / "anomaly_entry_grid_best_skip_reasons.csv", best_skip_reasons),
                        (output_dir / "anomaly_edge_health.csv", best_health),
                    ],
                    progress_label="anomaly artifacts: best grid files",
                )
                chart_status = (
                    render_anomaly_trade_charts(
                        best_trades,
                        config=best_config,
                        output_dir=output_dir / "charts" / "best_grid_variant",
                    )
                    if render_charts
                    else pd.DataFrame([{"status": "skipped", "reason": "render_charts_false"}])
                )
                _write_artifact_frames(
                    [(output_dir / "anomaly_trade_chart_status.csv", chart_status)],
                    progress_label="anomaly artifacts: chart status",
                )
    elif not trades.empty:
        health = build_edge_health_table(trades, label="primary")
        chart_status = (
            render_anomaly_trade_charts(
                trades,
                config=config,
                output_dir=output_dir / "charts" / "primary",
            )
            if render_charts
            else pd.DataFrame([{"status": "skipped", "reason": "render_charts_false"}])
        )
        _write_artifact_frames(
            [
                (output_dir / "anomaly_edge_health.csv", health),
                (output_dir / "anomaly_trade_chart_status.csv", chart_status),
            ],
            progress_label="anomaly artifacts: health chart status",
        )
    run_config = {
        **asdict(config),
        "feature_contract": config.feature_contract,
        "setup_timeframe": _effective_setup_timeframe(config),
        "entry_timeframe": _effective_entry_timeframe(config),
        "execution_model": _execution_model_label(config),
        "lab_config": asdict(config.lab_config),
    }
    _write_artifact_frames(
        [(output_dir / "run_config.csv", pd.DataFrame([run_config]))],
        progress_label="anomaly artifacts: run config",
    )
    return output_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(".output/cache"))
    parser.add_argument("--output-dir", type=Path, default=Path(".output/results/anomaly_lab"))
    parser.add_argument("--days", type=int, default=31)
    parser.add_argument("--timeframe", default="1m", help="Legacy single-timeframe mode; used as setup timeframe unless --setup-timeframe is set")
    parser.add_argument("--setup-timeframe", default=None, help="HTF setup timeframe, e.g. 5m")
    parser.add_argument("--entry-timeframe", default=None, help="LTF execution timeframe, e.g. 30s. If omitted, equals setup/timeframe")
    parser.add_argument("--end-timestamp-ms", type=int, default=None)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--baseline-candles", type=int, default=60)
    parser.add_argument("--confirmation-candles", type=int, default=4)
    parser.add_argument("--forward-high-candles", type=int, default=240)
    parser.add_argument("--forward-low-candles", type=int, default=60)
    parser.add_argument("--min-quote-ratio-start", type=float, default=5.0)
    parser.add_argument("--min-trade-ratio-start", type=float, default=5.0)
    parser.add_argument("--min-price-retention", type=float, default=0.70)
    parser.add_argument("--max-price-retention", type=float, default=None)
    parser.add_argument("--min-verticality-score", type=float, default=0.25)
    parser.add_argument("--min-hold-count", type=int, default=2)
    parser.add_argument("--min-oi-change-pct-3x5m", type=float, default=None)
    parser.add_argument("--require-oi-status-ok", action="store_true")
    parser.add_argument("--exhaustion-profile", choices=sorted(EXHAUSTION_PROFILES), default="none")
    parser.add_argument("--max-start-quote-ratio", type=float, default=None)
    parser.add_argument("--max-start-trade-ratio", type=float, default=None)
    parser.add_argument("--max-start-avg-trade-quote-size-ratio", type=float, default=None)
    parser.add_argument("--max-start-quote-ratio-per-abs-return", type=float, default=None)
    parser.add_argument("--max-start-trade-ratio-per-abs-return", type=float, default=None)
    parser.add_argument("--max-start-range-pct-ratio-to-baseline", type=float, default=None)
    parser.add_argument("--max-prior-up-down-whipsaw-to-impulse-range", type=float, default=0.60)
    parser.add_argument("--min-next-taker-buy-quote-share", type=float, default=None)
    parser.add_argument(
        "--red-flag-profile",
        choices=["none", "cautious", "strict", "runner_balanced", "runner_reclaim", "runner_flow"],
        default="none",
    )
    parser.add_argument("--min-mark-close-vs-decision-close-basis", type=float, default=None)
    parser.add_argument("--reject-oi-down-mark-discount", action="store_true")
    parser.add_argument("--reject-stale-derivatives-context", action="store_true")
    parser.add_argument("--max-start-taker-buy-quote-share-delta", type=float, default=None)
    parser.add_argument("--max-next-taker-buy-quote-share-delta", type=float, default=None)
    parser.add_argument("--min-flow-hold-count", type=int, default=None)
    parser.add_argument("--max-prior-spike-count-72h", type=int, default=None)
    parser.add_argument("--max-prior-fast-fade-count-72h", type=int, default=None)
    parser.add_argument("--min-start-lower-wick-to-range", type=float, default=None)
    parser.add_argument("--max-start-upper-wick-to-range", type=float, default=None)
    parser.add_argument("--max-initial-risk-pct", type=float, default=0.16)
    parser.add_argument("--entry-method", choices=["market", "break_box_high", "pullback_box_fraction"], default="market")
    parser.add_argument("--pullback-box-fraction", type=float, default=0.75)
    parser.add_argument("--entry-timeout-candles", type=int, default=60)
    parser.add_argument("--market-entry-latency-candles", type=int, default=1)
    parser.add_argument("--max-market-entry-drift-pct", type=float, default=0.003)
    parser.add_argument("--min-market-rr-to-signal-tp1", type=float, default=0.75)
    parser.add_argument("--tp1-r", type=float, default=1.0)
    parser.add_argument("--tp1-fraction", type=float, default=0.50)
    parser.add_argument("--trail-lookback-candles", type=int, default=5)
    parser.add_argument("--trail-buffer-r", type=float, default=0.10)
    parser.add_argument("--exit-rule", choices=sorted(EXIT_RULES), default="structural_trail")
    parser.add_argument("--max-hold-candles", type=int, default=240)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--render-charts", choices=["true", "false"], default="true")
    parser.add_argument("--run-entry-grid", action="store_true")
    parser.add_argument("--grid-oi3-values", default="0.01,0.02,0.03")
    parser.add_argument("--grid-hold-values", default="1,2")
    parser.add_argument("--grid-pullback-fractions", default="0.65,0.75,0.85")
    parser.add_argument("--grid-exhaustion-profiles", default="none")
    parser.add_argument("--grid-exit-rules", default="structural_trail")
    return parser


def config_from_args(args: argparse.Namespace) -> AnomalyBacktestConfig:
    lab_config = AnomalyLabConfig(
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        timeframe=args.setup_timeframe or args.timeframe,
        days=args.days,
        end_timestamp_ms=args.end_timestamp_ms,
        baseline_candles=args.baseline_candles,
        confirmation_candles=args.confirmation_candles,
        forward_high_candles=args.forward_high_candles,
        forward_low_candles=args.forward_low_candles,
        min_quote_ratio_start=args.min_quote_ratio_start,
        min_trade_ratio_start=args.min_trade_ratio_start,
    )
    return AnomalyBacktestConfig(
        lab_config=lab_config,
        setup_timeframe=args.setup_timeframe or args.timeframe,
        entry_timeframe=args.entry_timeframe or args.setup_timeframe or args.timeframe,
        feature_contract=(
            "htf_setup_ltf_entry_v1"
            if (args.entry_timeframe or args.setup_timeframe or args.timeframe) != (args.setup_timeframe or args.timeframe)
            else "closed_setup_tf_v1"
        ),
        min_price_retention=args.min_price_retention,
        max_price_retention=args.max_price_retention,
        min_verticality_score=args.min_verticality_score,
        min_hold_count=args.min_hold_count,
        min_oi_change_pct_3x5m=args.min_oi_change_pct_3x5m,
        require_oi_status_ok=bool(args.require_oi_status_ok),
        exhaustion_profile=args.exhaustion_profile,
        max_start_quote_ratio=args.max_start_quote_ratio,
        max_start_trade_ratio=args.max_start_trade_ratio,
        max_start_avg_trade_quote_size_ratio=args.max_start_avg_trade_quote_size_ratio,
        max_start_quote_ratio_per_abs_return=args.max_start_quote_ratio_per_abs_return,
        max_start_trade_ratio_per_abs_return=args.max_start_trade_ratio_per_abs_return,
        max_start_range_pct_ratio_to_baseline=args.max_start_range_pct_ratio_to_baseline,
        max_prior_up_down_whipsaw_to_impulse_range=args.max_prior_up_down_whipsaw_to_impulse_range,
        min_flow_hold_count=args.min_flow_hold_count,
        max_prior_spike_count_72h=args.max_prior_spike_count_72h,
        max_prior_fast_fade_count_72h=args.max_prior_fast_fade_count_72h,
        min_start_lower_wick_to_range=args.min_start_lower_wick_to_range,
        max_start_upper_wick_to_range=args.max_start_upper_wick_to_range,
        min_next_taker_buy_quote_share=args.min_next_taker_buy_quote_share,
        red_flag_profile=args.red_flag_profile,
        min_mark_close_vs_decision_close_basis=args.min_mark_close_vs_decision_close_basis,
        reject_oi_down_mark_discount=bool(args.reject_oi_down_mark_discount),
        reject_stale_derivatives_context=bool(args.reject_stale_derivatives_context),
        max_start_taker_buy_quote_share_delta=args.max_start_taker_buy_quote_share_delta,
        max_next_taker_buy_quote_share_delta=args.max_next_taker_buy_quote_share_delta,
        max_initial_risk_pct=args.max_initial_risk_pct,
        entry_method=args.entry_method,
        pullback_box_fraction=args.pullback_box_fraction,
        entry_timeout_candles=args.entry_timeout_candles,
        market_entry_latency_candles=args.market_entry_latency_candles,
        max_market_entry_drift_pct=args.max_market_entry_drift_pct,
        min_market_rr_to_signal_tp1=args.min_market_rr_to_signal_tp1,
        tp1_r=args.tp1_r,
        tp1_fraction=args.tp1_fraction,
        trail_lookback_candles=args.trail_lookback_candles,
        trail_buffer_r=args.trail_buffer_r,
        exit_rule=args.exit_rule,
        max_hold_candles=args.max_hold_candles,
        fee_rate=args.fee_rate,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = run_anomaly_strategy_backtest(
        config_from_args(args),
        symbols=args.symbols,
        run_entry_grid=bool(args.run_entry_grid),
        grid_oi3_values=_parse_grid_values(args.grid_oi3_values, cast=float),
        grid_hold_values=_parse_grid_values(args.grid_hold_values, cast=int),
        grid_pullback_fractions=_parse_grid_values(args.grid_pullback_fractions, cast=float),
        grid_exhaustion_profiles=_parse_grid_profile_values(args.grid_exhaustion_profiles),
        grid_exit_rules=_parse_grid_exit_rules(args.grid_exit_rules),
        render_charts=str(args.render_charts).lower() == "true",
    )
    print(f"wrote anomaly strategy artifacts to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
