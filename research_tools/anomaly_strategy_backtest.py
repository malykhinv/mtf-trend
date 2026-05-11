"""Research backtest for early anomaly-continuation long entries."""

from __future__ import annotations

import argparse
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
    CHART_5M_CANDLE_WIDTH,
    CHART_ANOMALY,
    CHART_DOWN,
    CHART_EMA9,
    CHART_EMA20,
    CHART_ENTRY,
    CHART_EXIT,
    CHART_FIGURE_FACE,
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
    draw_candles_on_columns,
    draw_price_zone,
    format_chart_symbol,
    format_timeframe_label,
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
    enrich_candidates_with_derivatives_context,
    _empty_context_columns,
    _emit_progress,
)

TRADE_SIGNAL_CONTEXT_COLUMNS = (
    "start_trade_count",
    "baseline_trade_count_median",
    "baseline_quote_volume_median",
    "start_trade_ratio",
    "start_quote_ratio",
    "next_n_trade_count_mean",
    "next_n_quote_volume_mean",
    "hold_count_next_n_candles",
    "hold_ratio_next_n_candles",
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
    min_price_retention: float = 0.70
    max_price_retention: float | None = None
    min_verticality_score: float = 0.25
    min_hold_count: int = 0
    min_oi_change_pct_3x5m: float | None = None
    require_oi_status_ok: bool = False
    exhaustion_profile: str = "none"
    max_start_quote_ratio: float | None = None
    max_start_trade_ratio: float | None = None
    max_start_avg_trade_quote_size_ratio: float | None = None
    max_start_quote_ratio_per_abs_return: float | None = None
    max_start_range_pct_ratio_to_baseline: float | None = None
    max_prior_up_down_whipsaw_to_impulse_range: float | None = 0.60
    min_next_taker_buy_quote_share: float | None = None
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


def build_anomaly_signals(
    candidates: pd.DataFrame,
    *,
    config: AnomalyBacktestConfig,
) -> pd.DataFrame:
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
        "min_next_taker_buy_quote_share": "next_n_taker_buy_quote_share_mean",
        "max_price_retention": "price_retention_next_n",
    }
    for config_field, column in optional_filter_columns.items():
        if getattr(config, config_field) is not None:
            required.add(column)
    missing = required.difference(candidates.columns)
    if missing:
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
    signals = signals.loc[mask].copy()
    if signals.empty:
        return signals
    signals.sort_values(["symbol", "decision_timestamp_ms"], inplace=True)
    signals.reset_index(drop=True, inplace=True)
    return signals


def _resolve_signal_stop(
    frame: pd.DataFrame,
    *,
    anomaly_timestamp_ms: int,
    decision_timestamp_ms: int,
    entry_price: float,
    config: AnomalyBacktestConfig,
) -> tuple[float, float, float]:
    box = frame.loc[
        (frame["timestamp"] >= anomaly_timestamp_ms)
        & (frame["timestamp"] <= decision_timestamp_ms)
    ]
    if box.empty:
        return float("nan"), float("nan"), float("nan")
    box_low = float(box["low"].min())
    box_high = float(box["high"].max())
    box_range = max(box_high - box_low, 0.0)
    previous_stop = box_low - config.stop_buffer_range_fraction * box_range
    entry_row = frame.loc[frame["timestamp"].eq(decision_timestamp_ms)]
    entry_ema20 = _safe_float(entry_row["ema20"].iloc[0]) if not entry_row.empty and "ema20" in entry_row.columns else None
    initial_stop = max(previous_stop, entry_ema20) if entry_ema20 is not None else previous_stop
    initial_risk = entry_price - initial_stop
    return initial_stop, initial_risk, box_range


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
    signal_tp1_price = decision_close + config.tp1_r * signal_risk if np.isfinite(signal_risk) else float("nan")

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
        drift_pct = _safe_divide(entry_price - decision_close, decision_close)
        abs_drift_pct = abs(drift_pct) if np.isfinite(drift_pct) else float("nan")
        actual_risk_at_signal_stop = entry_price - stop_at_decision
        rr_to_signal_tp1 = _safe_divide(signal_tp1_price - entry_price, actual_risk_at_signal_stop)
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


def simulate_long_signal(
    frame: pd.DataFrame,
    signal: pd.Series,
    *,
    config: AnomalyBacktestConfig,
) -> dict[str, object]:
    symbol = str(signal["symbol"])
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
        }
    if not np.isfinite(initial_risk) or initial_risk <= 0.0:
        return {
            "symbol": symbol,
            "decision_timestamp_ms": decision_ts,
            "decision_timestamp_utc": _timestamp_to_utc(decision_ts),
            "status": "skipped",
            "skip_reason": "invalid_initial_risk",
            "entry_method": config.entry_method,
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
        }

    tp1_price = entry_price + config.tp1_r * initial_risk
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
        "pullback_box_fraction": config.pullback_box_fraction if config.entry_method == "pullback_box_fraction" else np.nan,
        "entry_delay_candles": int((entry_ts - decision_ts) / 60_000),
        "entry_price": entry_price,
        "initial_stop": initial_stop,
        "initial_risk": initial_risk,
        "initial_risk_pct": initial_risk_pct,
        "box_range": box_range,
        "box_high": box_high,
        "tp1_price": tp1_price,
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
                }
            )
            continue
        frame = frame_cache.get(symbol)
        if frame is None:
            frame = _read_symbol_frame(config.lab_config.cache_dir, symbol, config.lab_config.timeframe)
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


def summarize_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame([{"metric": "closed_trades", "value": 0}])
    closed = trades.loc[trades["status"].eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame([{"metric": "closed_trades", "value": 0}])
    net = closed["net_return"].astype(float)
    gross_r = closed["gross_r"].astype(float)
    wins = net > 0
    rows = [
        {"metric": "closed_trades", "value": int(len(closed))},
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


def _entry_grid_signal_universe(
    candidates: pd.DataFrame,
    variants: Iterable[AnomalyBacktestConfig],
) -> pd.DataFrame:
    signal_frames: list[pd.DataFrame] = []
    for variant in variants:
        signals = build_anomaly_signals(candidates, config=variant)
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
        "min_next_taker_buy_quote_share": config.min_next_taker_buy_quote_share,
        "max_price_retention": config.max_price_retention,
        "signals": int(signal_count),
        "closed_trades": int(len(closed)),
        "skipped_trades": int(len(trades) - len(closed)),
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


def _build_anomaly_context(
    plot_frame: pd.DataFrame,
    timestamps: np.ndarray,
    x_values: np.ndarray,
    *,
    context_timeframe_ms: int,
) -> pd.DataFrame:
    if plot_frame.empty:
        return pd.DataFrame()
    prepared = plot_frame.copy()
    timeframe_ms = max(int(context_timeframe_ms), 1)
    prepared["bucket"] = (prepared["timestamp"].astype(np.int64) // timeframe_ms) * timeframe_ms
    aggregations: dict[str, str] = {
        "timestamp": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    for column in ("quote_volume", "number_of_trades", "taker_buy_volume", "taker_buy_quote_volume"):
        if column in prepared.columns:
            aggregations[column] = "sum"
    context = prepared.groupby("bucket", as_index=False).agg(aggregations)
    if context.empty:
        return context
    context["timestamp"] = context["bucket"].astype(np.int64)
    context["plot_x"] = np.interp(
        context["timestamp"].to_numpy(dtype=np.float64),
        timestamps.astype(np.float64),
        x_values,
    )
    return context


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


def _resolve_avg_trade_quote_series(frame: pd.DataFrame) -> np.ndarray | None:
    if "quote_volume" not in frame.columns or "number_of_trades" not in frame.columns:
        return None
    quote_volume = pd.to_numeric(frame["quote_volume"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    trade_count = pd.to_numeric(frame["number_of_trades"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    values = quote_volume / trade_count.replace(0.0, np.nan)
    if not values.notna().any():
        return None
    return values.fillna(0.0).to_numpy(dtype=np.float64, copy=False)


def _render_anomaly_trade_chart(
    *,
    frame: pd.DataFrame,
    trade: pd.Series,
    output_path: Path,
    pre_candles: int = 30,
    post_candles: int = 90,
    context_timeframe_ms: int | None = 300_000,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    anomaly_ts = _safe_int(trade.get("anomaly_timestamp_ms")) or _safe_int(trade.get("decision_timestamp_ms"))
    decision_ts = _safe_int(trade.get("decision_timestamp_ms")) or anomaly_ts
    entry_ts = _safe_int(trade.get("entry_timestamp_ms")) or decision_ts
    exit_ts = _safe_int(trade.get("exit_timestamp_ms")) or entry_ts
    if anomaly_ts is None or decision_ts is None or entry_ts is None or exit_ts is None:
        raise ValueError("missing_trade_timestamps")
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
    context_timeframe_ms = int(context_timeframe_ms or 300_000)
    context_frame = _build_anomaly_context(
        plot_frame,
        timestamps,
        x_values,
        context_timeframe_ms=context_timeframe_ms,
    )
    anomaly_idx = resolve_timestamp_plot_idx(timestamps, anomaly_ts)
    decision_idx = resolve_timestamp_plot_idx(timestamps, decision_ts)
    entry_idx = resolve_timestamp_plot_idx(timestamps, entry_ts)
    signal_entry_idx = resolve_timestamp_plot_idx(timestamps, signal_entry_ts)
    exit_idx = resolve_timestamp_plot_idx(timestamps, exit_ts)
    price_axis_right_x = float(len(plot_frame) - 0.5)

    fig, (ax_price, ax_context, ax_volume, ax_trades) = plt.subplots(
        4,
        1,
        figsize=TRADE_CHART_FIGSIZE,
        sharex=True,
        gridspec_kw={"height_ratios": [4, 2, 1, 1], "hspace": 0.05},
        facecolor=CHART_FIGURE_FACE,
    )
    configure_plot_axes(price_ax=ax_price, volume_ax=ax_volume, trades_ax=ax_trades)
    configure_plot_axes(price_ax=ax_context, volume_ax=ax_volume, trades_ax=ax_trades)

    draw_candles(ax_price, plot_frame, x_values)
    if not context_frame.empty:
        draw_candles_on_columns(
            ax_context,
            context_frame,
            x_column="plot_x",
            candle_width=max(
                CHART_5M_CANDLE_WIDTH,
                min(float(context_timeframe_ms) / max(float(timeframe_ms), 1.0) * 0.72, 12.0),
            ),
        )
    ax_price.plot(x_values, plot_frame["ema9"].to_numpy(dtype=np.float64), color=CHART_EMA9, linewidth=1.2, alpha=0.28, zorder=2.2)
    ax_price.plot(x_values, plot_frame["ema20"].to_numpy(dtype=np.float64), color=CHART_EMA20, linewidth=1.2, alpha=0.24, zorder=2.1)

    ax_price.axvline(anomaly_idx, color=CHART_ANOMALY, linewidth=0.95, alpha=0.30, zorder=5)
    ax_price.axvline(decision_idx, color=CHART_ENTRY, linewidth=0.9, alpha=0.18, linestyle="--", zorder=4.8)
    if signal_entry_price is not None and signal_entry_ts != entry_ts:
        ax_price.axvline(signal_entry_idx, color=CHART_MUTED, linewidth=0.9, alpha=0.30, linestyle=":", zorder=5.0)
    ax_price.axvline(entry_idx, color=CHART_ENTRY, linewidth=1.0, alpha=0.45, zorder=5.2)
    ax_price.axvline(exit_idx, color=CHART_EXIT, linewidth=1.0, alpha=0.52, linestyle="-.", zorder=5.3)
    ax_context.axvline(anomaly_idx, color=CHART_ANOMALY, linewidth=0.9, alpha=0.22, zorder=5)
    ax_context.axvline(entry_idx, color=CHART_ENTRY, linewidth=0.9, alpha=0.32, zorder=5)
    ax_context.axvline(exit_idx, color=CHART_EXIT, linewidth=0.9, alpha=0.35, linestyle="-.", zorder=5)

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

    tag_input = [
        ("TP1", tp1_price),
        ("Entry", entry_price),
        ("SL", initial_stop),
        ("Exit", exit_price),
    ]
    if signal_entry_price is not None and signal_entry_ts != entry_ts:
        tag_input.append(("Signal", signal_entry_price))
    tag_positions = resolve_axis_tag_positions(tag_input)
    tag_specs = [
        ("TP1", tp1_price, CHART_PROFIT_EDGE, entry_idx - 0.5),
        ("Entry", entry_price, CHART_ENTRY, entry_idx),
        ("SL", initial_stop, CHART_RISK_EDGE, entry_idx - 0.5),
        ("Exit", exit_price, CHART_EXIT, exit_idx),
    ]
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
    volume = plot_frame[volume_column].fillna(0.0).to_numpy(dtype=np.float64)
    volume_max = float(np.nanmax(volume)) if volume.size else 0.0
    volume_pct = (volume / volume_max) * 100.0 if volume_max > 0.0 else np.zeros_like(volume)
    opens = plot_frame["open"].to_numpy(dtype=np.float64)
    closes = plot_frame["close"].to_numpy(dtype=np.float64)
    colors = np.where(closes >= opens, CHART_UP, CHART_DOWN)
    ax_volume.bar(
        x_values,
        volume_pct,
        width=resolve_candle_width(x_values, default=0.82),
        color=colors,
        edgecolor="none",
        alpha=0.82,
        zorder=3,
    )

    avg_trade_quote = _resolve_avg_trade_quote_series(plot_frame)
    avg_trade_quote_max = float(np.nanmax(avg_trade_quote)) if avg_trade_quote is not None and avg_trade_quote.size else 0.0
    if avg_trade_quote is not None and avg_trade_quote_max > 0.0:
        avg_trade_quote_pct = (avg_trade_quote / avg_trade_quote_max) * 100.0
        ax_trades.bar(
            x_values,
            avg_trade_quote_pct,
            width=resolve_candle_width(x_values, default=0.82),
            color=colors,
            edgecolor="none",
            alpha=0.78,
            zorder=3,
        )
    else:
        _annotate_anomaly_panel_message(ax_trades, "quote_volume / number_of_trades missing")

    high_values = plot_frame["high"].to_numpy(dtype=np.float64)
    low_values = plot_frame["low"].to_numpy(dtype=np.float64)
    padding = max((float(np.nanmax(high_values)) - float(np.nanmin(low_values))) * 0.05, 1e-9)
    ax_price.set_ylim(float(np.nanmin(low_values)) - padding, float(np.nanmax(high_values)) + padding)
    if not context_frame.empty:
        context_high = context_frame["high"].to_numpy(dtype=np.float64)
        context_low = context_frame["low"].to_numpy(dtype=np.float64)
        context_padding = max((float(np.nanmax(context_high)) - float(np.nanmin(context_low))) * 0.08, 1e-9)
        ax_context.set_ylim(float(np.nanmin(context_low)) - context_padding, float(np.nanmax(context_high)) + context_padding)
    entry_candle_width = resolve_candle_width(x_values)
    ax_price.set_xlim(-max(0.5, entry_candle_width * 0.65), len(plot_frame) - 1 + max(0.5, entry_candle_width * 0.65))
    ax_context.set_xlim(ax_price.get_xlim())
    ax_volume.set_ylim(0.0, 100.0)
    ax_volume.set_yticks([0.0, 50.0, 100.0])
    ax_volume.set_yticklabels(["0", "50", "100"], color=CHART_MUTED)
    ax_trades.set_ylim(0.0, 100.0)
    ax_trades.set_yticks([0.0, 50.0, 100.0])
    ax_trades.set_yticklabels(["0", "50", "100"], color=CHART_MUTED)
    ax_price.set_ylabel(format_timeframe_label(infer_frame_step_ms(plot_frame)))
    ax_context.set_ylabel(format_timeframe_label(context_timeframe_ms))
    ax_volume.set_ylabel("Quote vol %")
    ax_trades.set_ylabel("Quote / trade %")
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

    tick_timestamps = build_tick_timestamps(plot_frame)
    tick_positions = build_tick_positions_from_timestamps(tick_timestamps, len(plot_frame))
    tick_labels = build_tick_labels_from_timestamps(tick_timestamps, tick_positions)
    ax_trades.set_xticks(tick_positions)
    ax_trades.set_xticklabels(tick_labels)
    ax_price.tick_params(axis="x", labelbottom=False)
    ax_context.tick_params(axis="x", labelbottom=False)
    ax_volume.tick_params(axis="x", labelbottom=False)
    ax_price.margins(x=0.01)
    ax_context.margins(x=0.0)
    ax_volume.margins(x=0.0)
    ax_trades.margins(x=0.0)
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
                frame = _read_symbol_frame(config.lab_config.cache_dir, symbol, config.lab_config.timeframe)
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
    run_entry_grid: bool = False,
    grid_oi3_values: Iterable[float] = (0.01, 0.02, 0.03),
    grid_hold_values: Iterable[int] = (1, 2),
    grid_pullback_fractions: Iterable[float] = (0.65, 0.75, 0.85),
    grid_exhaustion_profiles: Iterable[str] = ("none",),
    grid_exit_rules: Iterable[str] = ("structural_trail",),
    derivatives_context_fetcher: object | None = None,
) -> Path:
    output_dir = config.lab_config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = collect_anomaly_lab_rows(
        config.lab_config,
        symbols=symbols,
        progress_label="anomaly candidates",
        include_derivatives_context=derivatives_context_fetcher is None,
    )
    print("anomaly signals: filtering", flush=True)
    signals = build_anomaly_signals(candidates, config=config)
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
            context_signals = signals.loc[:, ["symbol", "decision_timestamp_ms"]].copy()
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
        signals = build_anomaly_signals(candidates, config=config)
    _write_artifact_frames(
        [
            (output_dir / "anomaly_candidates.csv", candidates),
            (output_dir / "oi_context_status.csv", build_oi_context_status(candidates)),
            (output_dir / "market_context_status.csv", build_derivatives_context_status(candidates)),
            (output_dir / "anomaly_signals.csv", signals),
        ],
        progress_label="anomaly artifacts: base files",
    )
    print(f"anomaly trades: simulating {len(signals)} signals", flush=True)
    trades = simulate_anomaly_trades(signals, config=config, progress_label="anomaly trades")
    summary = summarize_trades(trades)
    _write_artifact_frames(
        [
            (output_dir / "anomaly_trades.csv", trades),
            (output_dir / "anomaly_profitability_summary.csv", summary),
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
                _write_artifact_frames(
                    [
                        (output_dir / "anomaly_entry_grid_best_trades.csv", best_trades),
                        (output_dir / "anomaly_entry_grid_best_config.csv", best_config_frame),
                        (output_dir / "anomaly_edge_health.csv", best_health),
                    ],
                    progress_label="anomaly artifacts: best grid files",
                )
                chart_status = render_anomaly_trade_charts(
                    best_trades,
                    config=best_config,
                    output_dir=output_dir / "charts" / "best_grid_variant",
                )
                _write_artifact_frames(
                    [(output_dir / "anomaly_trade_chart_status.csv", chart_status)],
                    progress_label="anomaly artifacts: chart status",
                )
    elif not trades.empty:
        health = build_edge_health_table(trades, label="primary")
        chart_status = render_anomaly_trade_charts(
            trades,
            config=config,
            output_dir=output_dir / "charts" / "primary",
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
    parser.add_argument("--timeframe", default="1m")
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
    parser.add_argument("--min-hold-count", type=int, default=0)
    parser.add_argument("--min-oi-change-pct-3x5m", type=float, default=None)
    parser.add_argument("--require-oi-status-ok", action="store_true")
    parser.add_argument("--exhaustion-profile", choices=sorted(EXHAUSTION_PROFILES), default="none")
    parser.add_argument("--max-start-quote-ratio", type=float, default=None)
    parser.add_argument("--max-start-trade-ratio", type=float, default=None)
    parser.add_argument("--max-start-avg-trade-quote-size-ratio", type=float, default=None)
    parser.add_argument("--max-start-quote-ratio-per-abs-return", type=float, default=None)
    parser.add_argument("--max-start-range-pct-ratio-to-baseline", type=float, default=None)
    parser.add_argument("--max-prior-up-down-whipsaw-to-impulse-range", type=float, default=0.60)
    parser.add_argument("--min-next-taker-buy-quote-share", type=float, default=None)
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
        timeframe=args.timeframe,
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
        max_start_range_pct_ratio_to_baseline=args.max_start_range_pct_ratio_to_baseline,
        max_prior_up_down_whipsaw_to_impulse_range=args.max_prior_up_down_whipsaw_to_impulse_range,
        min_next_taker_buy_quote_share=args.min_next_taker_buy_quote_share,
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
    )
    print(f"wrote anomaly strategy artifacts to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
