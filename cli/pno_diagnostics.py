"""PNO diagnostics, plotting and research helpers."""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.patches import Rectangle
from matplotlib.ticker import LinearLocator
from matplotlib.transforms import blended_transform_factory

from strategy.pno.engine import PNO_STAGE_1_PUMP, PNO_STAGE_4_LEVEL, PNO_STAGE_5_TRADE, PNO_STAGE_SEQUENCE
from vectorbt_runner import SymbolMtfFrames

_PNO_STAGE1_REVIEW_MAX_ROWS_PER_REASON = 8
_PNO_STAGE1_REVIEW_MAX_ROWS_PER_SYMBOL = 2
_PNO_STAGE1_REVIEW_FALLBACK_ROWS_PER_REASON = 4
_PNO_STAGE1_REVIEW_DISTANCE_MAX = 0.18
_PNO_PLOT_TRADE_COUNT_COLUMNS: tuple[str, ...] = ("number_of_trades", "trades", "trade_count")
_PNO_STAGE1_REJECTION_DISTANCE_FIELDS: dict[str, tuple[str, str, str]] = {
    "impulse_atr_pre_too_small": ("pump_impulse_atr_pre", "stage1_min_impulse_atr_pre", "min"),
    "peak_bar_tr_atr_pre_too_small": ("pump_peak_bar_tr_atr_pre", "stage1_min_peak_bar_tr_atr_pre", "min"),
    "volume_ratio_start_too_small": ("pump_volume_ratio_start", "stage1_min_volume_ratio_start", "min"),
    "trade_ratio_start_too_small": ("pump_trade_ratio_start", "stage1_min_trade_ratio_start", "min"),
    "volume_ratio_continue_too_small": ("pump_volume_ratio_continue", "stage1_min_volume_ratio_continue", "min"),
    "trade_ratio_continue_too_small": ("pump_trade_ratio_continue", "stage1_min_trade_ratio_continue", "min"),
    "flow_hold_not_confirmed": ("flow_hold_bar_count", "stage1_flow_hold_bars", "min"),
    "flow_candidate_failed_stage1_confirmation": (
        "pump_trade_ratio_start",
        "stage1_min_trade_ratio_start",
        "min",
    ),
    "active_flow_faded_before_structure": (
        "active_context_quote_ratio",
        "stage1_active_context_min_baseline_ratio",
        "min",
    ),
    "path_efficiency_too_low": ("pump_path_efficiency", "stage1_min_path_efficiency", "min"),
    "wick_share_too_high": ("pump_wick_share", "stage1_max_wick_share", "max"),
    "body_share_mean_too_low": ("pump_body_share_mean", "stage1_min_body_share_mean", "min"),
    "flat_body_share_too_high": ("pump_flat_body_share", "stage1_max_flat_body_share", "max"),
    "body_wick_edge_too_low": ("pump_body_wick_edge", "stage1_min_body_wick_edge", "min"),
    "micro_flat_bar_share_too_high": ("pump_micro_flat_bar_share", "stage1_max_micro_flat_bar_share", "max"),
    "active_high_upper_wick_too_high": (
        "active_high_bar_upper_wick_share",
        "stage1_max_active_high_upper_wick_share",
        "max",
    ),
    "red_body_share_5m_too_high": ("pump_max_red_body_share_5m", "stage1_max_red_body_share_5m", "max"),
    "counterflow_ratio_5m_too_high": ("pump_counterflow_ratio_5m", "stage1_max_counterflow_ratio_5m", "max"),
    "red_body_share_1m_too_high": ("pump_max_red_body_share_1m", "stage1_max_red_body_share_1m", "max"),
    "counterflow_ratio_1m_too_high": ("pump_counterflow_ratio_1m", "stage1_max_counterflow_ratio_1m", "max"),
    "cumulative_quote_volume_too_low": ("cumulative_quote_volume", "stage1_min_cumulative_quote_volume", "min"),
    "pre_pump_ema_crosses_too_low": ("pre_pump_ema_crosses_1h", "stage1_pre_pump_ema_crosses_min", "min"),
    "barcode_fraction_too_high": ("pre_pump_barcode_fraction_1h", "stage1_barcode_max_fraction_1h", "max"),
    "pre_pump_high_24h_above_active_high": ("pre_pump_high_24h", "active_high", "max"),
    "pre_pump_high_1h_too_high": ("pre_pump_high_1h", "stage1_pre_pump_high_cap", "max"),
    "pump_candidate_barcode": ("pre_pump_barcode_fraction_1h", "stage1_barcode_max_fraction_1h", "max"),
    "pump_candidate_jerky": ("pump_path_efficiency", "stage1_min_path_efficiency", "min"),
}

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
        return "н/д"
    total_seconds = int(round(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}ч {minutes:02d}м"
    if minutes > 0:
        return f"{minutes}м {secs:02d}с"
    return f"{secs}с"


def _resolve_metric_distance(
    actual: float | None,
    threshold: float | None,
    *,
    direction: str,
) -> float | None:
    if actual is None or threshold is None:
        return None
    denominator = max(abs(threshold), 1e-9)
    if direction == "min":
        gap = threshold - actual
    else:
        gap = actual - threshold
    if gap < 0.0:
        gap = 0.0
    return gap / denominator


def _resolve_stage1_rejection_review_score(row: dict[str, object]) -> float | None:
    reason = str(row.get("reason") or "")
    fields = _PNO_STAGE1_REJECTION_DISTANCE_FIELDS.get(reason)
    if fields is not None:
        actual = _safe_float(row.get(fields[0]))
        threshold = _safe_float(row.get(fields[1]))
        return _resolve_metric_distance(actual, threshold, direction=fields[2])

    if reason == "pump_candidate_below_ema20":
        current_close = _safe_float(row.get("current_close_5m"))
        current_ema20 = _safe_float(row.get("current_ema20_5m"))
        leg_size = _safe_float(row.get("leg_size"))
        pump_pct = _safe_float(row.get("pump_pct"))
        min_pump_pct = _safe_float(row.get("stage1_min_pump_pct"))
        if (
            current_close is None
            or current_ema20 is None
            or leg_size is None
            or leg_size <= 0.0
            or pump_pct is None
            or min_pump_pct is None
            or pump_pct <= min_pump_pct
        ):
            return None
        return max(current_ema20 - current_close, 0.0) / max(leg_size, 1e-9)

    if reason == "hold_floor_lost":
        current_close = _safe_float(row.get("current_close"))
        hold_floor = _safe_float(row.get("hold_floor"))
        reference_high = _safe_float(row.get("reference_high"))
        leg_start = _safe_float(row.get("leg_start"))
        if current_close is None or hold_floor is None or reference_high is None or leg_start is None:
            return None
        return max(hold_floor - current_close, 0.0) / max(reference_high - leg_start, 1e-9)

    if reason == "leg_start_lost":
        current_low = _safe_float(row.get("current_low"))
        leg_start = _safe_float(row.get("leg_start"))
        reference_high = _safe_float(row.get("reference_high"))
        if current_low is None or leg_start is None or reference_high is None:
            return None
        return max(leg_start - current_low, 0.0) / max(reference_high - leg_start, 1e-9)

    return None


def _cap_stage1_review_rows(
    rows: list[dict[str, object]],
    *,
    max_rows: int,
    max_rows_per_symbol: int,
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    symbol_counts: dict[str, int] = {}
    ordered_rows = sorted(
        rows,
        key=lambda item: (
            _safe_int(item.get("timestamp_ms")) if _safe_int(item.get("timestamp_ms")) is not None else 0,
            str(item.get("symbol") or ""),
        ),
    )
    for row in ordered_rows:
        symbol = str(row.get("symbol") or "")
        if symbol and symbol_counts.get(symbol, 0) >= max_rows_per_symbol:
            continue
        selected.append(row)
        if symbol:
            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
        if len(selected) >= max_rows:
            break
    return selected


def _select_stage1_review_rejection_rows(
    *,
    reason: str,
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    scored_rows: list[tuple[float, dict[str, object]]] = []
    fallback_rows: list[dict[str, object]] = []
    for row in rows:
        score = _resolve_stage1_rejection_review_score(row)
        if score is None or not np.isfinite(score):
            fallback_rows.append(row)
            continue
        if score > _PNO_STAGE1_REVIEW_DISTANCE_MAX:
            continue
        scored_rows.append((score, row))

    if scored_rows:
        scored_rows.sort(
            key=lambda item: (
                item[0],
                _safe_int(item[1].get("timestamp_ms")) if _safe_int(item[1].get("timestamp_ms")) is not None else 0,
                str(item[1].get("symbol") or ""),
            )
        )
        return _cap_stage1_review_rows(
            [row for _, row in scored_rows],
            max_rows=_PNO_STAGE1_REVIEW_MAX_ROWS_PER_REASON,
            max_rows_per_symbol=_PNO_STAGE1_REVIEW_MAX_ROWS_PER_SYMBOL,
        )

    if reason in {"hold_floor_lost_before_stage2", "hold_floor_wick_before_stage2", "stage1_lost_before_stage2"}:
        return _cap_stage1_review_rows(
            fallback_rows or rows,
            max_rows=_PNO_STAGE1_REVIEW_FALLBACK_ROWS_PER_REASON,
            max_rows_per_symbol=1,
        )

    return []


def _select_stage_review_rejection_rows(
    *,
    stage_id: str,
    reason: str,
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    if stage_id != PNO_STAGE_1_PUMP:
        return list(rows)
    return _select_stage1_review_rejection_rows(reason=reason, rows=rows)


def _build_pno_plot_frame(
    *,
    levels_frame: pd.DataFrame,
    entry_frame: pd.DataFrame,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> pd.DataFrame:
    selected_columns = [column for column in ("timestamp", "open", "high", "low", "close", "volume", *_PNO_PLOT_TRADE_COUNT_COLUMNS) if column in entry_frame.columns]
    plot_frame = entry_frame.loc[
        (entry_frame["timestamp"] >= start_timestamp_ms) & (entry_frame["timestamp"] <= end_timestamp_ms),
        selected_columns,
    ].copy()
    if plot_frame.empty:
        return plot_frame
    if {"ema9", "ema20"}.issubset(plot_frame.columns):
        return _with_canonical_pno_trade_count(plot_frame)

    levels_ema = _prepare_pno_levels_ema_source(levels_frame)
    if levels_ema.empty:
        plot_frame["ema9"] = np.nan
        plot_frame["ema20"] = np.nan
        return _with_canonical_pno_trade_count(plot_frame)

    levels_timestamps = levels_ema["timestamp"].to_numpy(dtype=np.float64)
    plot_timestamps = plot_frame["timestamp"].to_numpy(dtype=np.float64)
    plot_frame["ema9"] = np.interp(plot_timestamps, levels_timestamps, levels_ema["ema9"].to_numpy(dtype=np.float64))
    plot_frame["ema20"] = np.interp(plot_timestamps, levels_timestamps, levels_ema["ema20"].to_numpy(dtype=np.float64))
    return _with_canonical_pno_trade_count(plot_frame)


def _with_canonical_pno_trade_count(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    source_column = next(
        (
            column
            for column in _PNO_PLOT_TRADE_COUNT_COLUMNS
            if column in frame.columns and pd.to_numeric(frame[column], errors="coerce").notna().any()
        ),
        None,
    )
    if source_column is None:
        return frame
    prepared = frame.copy()
    source_values = pd.to_numeric(prepared[source_column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    for column in _PNO_PLOT_TRADE_COUNT_COLUMNS:
        if column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce").replace(
                [np.inf, -np.inf],
                np.nan,
            ).combine_first(source_values)
        else:
            prepared[column] = source_values
    return prepared


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
    prepared = _with_canonical_pno_trade_count(prepared)
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
    prepared = _with_canonical_pno_trade_count(prepared)
    return prepared


def _prepare_pno_entry_plot_source_with_ema(*, levels_frame: pd.DataFrame, entry_frame: pd.DataFrame) -> pd.DataFrame:
    prepared = _prepare_pno_entry_plot_source(entry_frame)
    if prepared.empty:
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
_PNO_PLOT_PROTECT_FACE = "#14532d"
_PNO_PLOT_PROTECT_EDGE = "#86efac"
_PNO_PLOT_BASE_FACE = "#1d4ed8"
_PNO_PLOT_BASE_EDGE = "#60a5fa"
_PNO_PLOT_PUMP_ZONE_FACE = "#facc15"
_PNO_PLOT_PUMP_ZONE_EDGE = "#fde68a"
_PNO_PLOT_STRUCTURE_LINE = "#e2e8f0"
_PNO_PLOT_STRUCTURE_INSET_LINE = "#facc15"
_PNO_PLOT_STRUCTURE_INSET_BOS = "#38bdf8"
_PNO_PLOT_STRUCTURE_HIGH = "#fda4af"
_PNO_PLOT_STRUCTURE_LOW = "#7dd3fc"
_PNO_PLOT_PANEL_EDGE = "#1e293b"
_PNO_PLOT_CANDLE_WIDTH = 0.64
_PNO_PLOT_5M_CANDLE_WIDTH = 4.0
_PNO_PLOT_MAX_X_TICKS = 8
_PNO_TRADE_CHART_FIGSIZE = (8.0, 8.0)
_PNO_TRADE_CHART_HEIGHT_RATIOS = [4, 2, 1, 1]
_PNO_TRADE_SLEEP_LOOKBACK_BARS = 12
_PNO_STAGE_REVIEW_FIGSIZE = (12.8, 6.4)
_PNO_STAGE_REVIEW_HEIGHT_RATIOS = [4.4, 1.15, 1.0]
_PNO_STAGE_REVIEW_SLEEP_BASELINE_MS = 24 * 60 * 60_000
_PNO_STAGE_REVIEW_SLEEP_CONTEXT_BARS = 24
_PNO_STAGE_REVIEW_MAX_ENTRY_LOOKBACK_MS = 6 * 60 * 60_000
_PNO_STAGE_REVIEW_BASELINE_COLOR = "#facc15"
_PNO_RESEARCH_TRADE_PATH_MAX_BARS = 240
_PNO_PLOT_AXIS_TAG_LABEL_WIDTH = 7
_PNO_PLOT_AXIS_TAG_TEXT_WIDTH = 20
_PNO_PLOT_SAVEFIG_KWARGS = {"dpi": 100, "facecolor": _PNO_PLOT_FIGURE_FACE, "pil_kwargs": {"compress_level": 1}}
_PNO_STAGE_REVIEW_SAVEFIG_KWARGS = {"dpi": 72, "facecolor": _PNO_PLOT_FIGURE_FACE, "pil_kwargs": {"compress_level": 1}}


def _resolve_pno_candle_width(x_values: np.ndarray, *, default: float = _PNO_PLOT_CANDLE_WIDTH) -> float:
    finite_x = np.asarray(x_values[np.isfinite(x_values)], dtype=np.float64)
    if finite_x.size < 2:
        return float(default)
    diffs = np.diff(np.sort(np.unique(finite_x)))
    diffs = diffs[diffs > 0.0]
    if diffs.size == 0:
        return float(default)
    return max(min(float(np.median(diffs)) * 0.72, float(np.min(diffs)) * 0.92), 0.18)


def _draw_pno_candles(
    ax: plt.Axes,
    frame: pd.DataFrame,
    x_values: np.ndarray,
    *,
    up_color: str = _PNO_PLOT_UP,
    down_color: str = _PNO_PLOT_DOWN,
    wick_linewidth: float = 1.0,
    body_linewidth: float = 0.8,
    alpha: float = 1.0,
    zorder: float = 3,
) -> None:
    if frame.empty or len(x_values) == 0:
        return
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
    wick_colors = np.where(up_mask, up_color, down_color)
    ax.add_collection(
        LineCollection(
            wick_segments,
            colors=wick_colors.tolist(),
            linewidths=wick_linewidth,
            alpha=alpha,
            zorder=zorder,
        )
    )
    body_lows = np.minimum(opens, closes)
    body_heights = np.maximum(np.abs(closes - opens), 1e-9)
    candle_width = _resolve_pno_candle_width(x_values)
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
            linewidth=body_linewidth,
            alpha=alpha,
            zorder=zorder + 1,
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


def _resolve_pno_plot_index_span(
    timestamps: np.ndarray,
    *,
    start_timestamp_ms: int | None,
    end_timestamp_ms: int | None,
) -> tuple[int, int] | None:
    if start_timestamp_ms is None or end_timestamp_ms is None or timestamps.size == 0:
        return None
    if end_timestamp_ms < start_timestamp_ms:
        end_timestamp_ms = start_timestamp_ms
    start_idx = int(np.searchsorted(timestamps, int(start_timestamp_ms), side="left"))
    end_idx = int(np.searchsorted(timestamps, int(end_timestamp_ms), side="right") - 1)
    start_idx = max(0, min(start_idx, len(timestamps) - 1))
    end_idx = max(start_idx, min(end_idx, len(timestamps) - 1))
    return start_idx, end_idx


def _draw_pno_price_zone(
    ax: plt.Axes,
    *,
    timestamps: np.ndarray,
    start_timestamp_ms: int | None,
    end_timestamp_ms: int | None,
    low: float | None,
    high: float | None,
    facecolor: str,
    edgecolor: str,
    alpha: float = 0.14,
    linewidth: float = 0.9,
    linestyle: str = "-",
    zorder: float = 2.4,
) -> None:
    if low is None or high is None:
        return
    span = _resolve_pno_plot_index_span(
        timestamps,
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=end_timestamp_ms,
    )
    if span is None:
        return
    start_idx, end_idx = span
    zone_low = min(float(low), float(high))
    zone_high = max(float(low), float(high))
    ax.add_patch(
        Rectangle(
            (start_idx - 0.45, zone_low),
            max(float(end_idx - start_idx + 1), 1.0),
            max(zone_high - zone_low, 1e-9),
            facecolor=facecolor,
            edgecolor=edgecolor,
            linewidth=linewidth,
            linestyle=linestyle,
            alpha=alpha,
            zorder=zorder,
        )
    )


def _detect_pno_pump_pause_zones(
    frame: pd.DataFrame,
    *,
    start_timestamp_ms: int | None,
    end_timestamp_ms: int | None,
    max_zones: int = 3,
) -> list[dict[str, float | int]]:
    if frame.empty or start_timestamp_ms is None or end_timestamp_ms is None:
        return []
    window = frame.loc[
        (frame["timestamp"] >= int(start_timestamp_ms)) & (frame["timestamp"] <= int(end_timestamp_ms))
    ].copy()
    if len(window) < 4:
        return []
    highs = window["high"].to_numpy(dtype=np.float64)
    lows = window["low"].to_numpy(dtype=np.float64)
    closes = window["close"].to_numpy(dtype=np.float64)
    tr = np.maximum(highs - lows, 1e-12)
    atr_ref = float(np.nanmedian(tr)) if tr.size else np.nan
    if not np.isfinite(atr_ref) or atr_ref <= 0.0:
        return []
    candidates: list[dict[str, float | int]] = []
    for length in (5, 4, 3):
        if len(window) < length:
            continue
        for offset in range(0, len(window) - length + 1):
            local_high = float(np.nanmax(highs[offset : offset + length]))
            local_low = float(np.nanmin(lows[offset : offset + length]))
            zone_range = local_high - local_low
            close_drift = abs(float(closes[offset + length - 1]) - float(closes[offset]))
            if zone_range <= 1.35 * atr_ref and close_drift <= 0.75 * zone_range:
                start_ts = int(window["timestamp"].iloc[offset])
                end_ts = int(window["timestamp"].iloc[offset + length - 1])
                candidates.append(
                    {
                        "start_timestamp_ms": start_ts,
                        "end_timestamp_ms": end_ts,
                        "low": local_low,
                        "high": local_high,
                        "score": float(length) / max(zone_range / atr_ref, 0.25),
                    }
                )
    if not candidates:
        return []
    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    selected: list[dict[str, float | int]] = []
    for candidate in candidates:
        cand_start = int(candidate["start_timestamp_ms"])
        cand_end = int(candidate["end_timestamp_ms"])
        overlaps = any(
            not (cand_end < int(existing["start_timestamp_ms"]) or cand_start > int(existing["end_timestamp_ms"]))
            for existing in selected
        )
        if overlaps:
            continue
        selected.append(candidate)
        if len(selected) >= max_zones:
            break
    selected.sort(key=lambda item: int(item["start_timestamp_ms"]))
    return selected


def _draw_pno_pump_pause_zones(
    ax: plt.Axes,
    *,
    timestamps: np.ndarray,
    plot_frame: pd.DataFrame,
    pump_start_timestamp_ms: int | None,
    active_high_timestamp_ms: int | None,
    alpha: float = 0.11,
    zorder: float = 2.15,
) -> None:
    for zone in _detect_pno_pump_pause_zones(
        plot_frame,
        start_timestamp_ms=pump_start_timestamp_ms,
        end_timestamp_ms=active_high_timestamp_ms,
    ):
        _draw_pno_price_zone(
            ax,
            timestamps=timestamps,
            start_timestamp_ms=int(zone["start_timestamp_ms"]),
            end_timestamp_ms=int(zone["end_timestamp_ms"]),
            low=float(zone["low"]),
            high=float(zone["high"]),
            facecolor=_PNO_PLOT_PUMP_ZONE_FACE,
            edgecolor=_PNO_PLOT_PUMP_ZONE_EDGE,
            alpha=alpha,
            linewidth=0.75,
            linestyle=":",
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
    try:
        base.encode("latin-1")
    except UnicodeEncodeError:
        base = _sanitize_plot_name(base.replace("/", "_"))
    return base


def _format_pno_timeframe_label(step_ms: int) -> str:
    if step_ms <= 0:
        return "TF"
    if step_ms % 60_000 == 0:
        minutes = step_ms // 60_000
        if minutes % 60 == 0:
            hours = minutes // 60
            return f"{hours}h"
        return f"{minutes}m"
    seconds = max(step_ms // 1000, 1)
    return f"{seconds}s"


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


def _configure_pno_plot_axes(*, price_ax: plt.Axes, volume_ax: plt.Axes, trades_ax: plt.Axes | None = None) -> None:
    axes = [price_ax, volume_ax]
    if trades_ax is not None:
        axes.append(trades_ax)
    for axis in axes:
        axis.set_facecolor(_PNO_PLOT_AXIS_FACE)
        axis.grid(True, color=_PNO_PLOT_GRID, linewidth=0.7, alpha=0.18)
        axis.tick_params(axis="both", colors=_PNO_PLOT_MUTED, labelsize=8, length=0, pad=6)
        for spine in axis.spines.values():
            spine.set_color(_PNO_PLOT_PANEL_EDGE)
            spine.set_linewidth(1.0)
    price_ax.yaxis.label.set_color(_PNO_PLOT_MUTED)
    volume_ax.yaxis.label.set_color(_PNO_PLOT_MUTED)
    if trades_ax is not None:
        trades_ax.yaxis.label.set_color(_PNO_PLOT_MUTED)
    price_ax.yaxis.set_major_locator(LinearLocator(6))
    volume_ax.yaxis.set_major_locator(LinearLocator(3))
    price_ax.yaxis.set_label_coords(-0.072, 0.5)
    volume_ax.yaxis.set_label_coords(-0.072, 0.5)
    if trades_ax is not None:
        trades_ax.yaxis.set_major_locator(LinearLocator(3))
        trades_ax.yaxis.set_label_coords(-0.072, 0.5)
    price_ax.yaxis.set_ticks_position("right")
    price_ax.yaxis.tick_right()
    price_ax.tick_params(axis="y", labelleft=False, labelright=True)
    volume_ax.yaxis.set_ticks_position("right")
    volume_ax.yaxis.tick_right()
    volume_ax.tick_params(axis="y", labelleft=False, labelright=True)
    if trades_ax is not None:
        trades_ax.yaxis.set_ticks_position("right")
        trades_ax.yaxis.tick_right()
        trades_ax.tick_params(axis="y", labelleft=False, labelright=True)


def _resolve_trade_count_series(frame: pd.DataFrame) -> np.ndarray:
    trade_count_column = next(
        (column for column in ("number_of_trades", "trades", "trade_count") if column in frame.columns),
        None,
    )
    if trade_count_column is None:
        return np.zeros(len(frame), dtype=np.float64)
    values = pd.to_numeric(frame[trade_count_column], errors="coerce").fillna(0.0)
    return values.to_numpy(dtype=np.float64, copy=False)


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


def _coerce_pno_sequence(raw: object) -> list[object]:
    if raw is None:
        return []
    if isinstance(raw, np.ndarray):
        return raw.tolist()
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
            except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, (list, tuple)):
                return list(parsed)
        return []
    return []


def _resolve_pno_structure_points(row: dict[str, object]) -> list[tuple[int, float, str]]:
    timestamps_ms = _coerce_pno_sequence(row.get("structure_pivot_timestamps_ms"))
    prices = _coerce_pno_sequence(row.get("structure_pivot_prices"))
    kinds = _coerce_pno_sequence(row.get("structure_pivot_kinds"))
    points: list[tuple[int, float, str]] = []
    for timestamp_ms, price, kind in zip(timestamps_ms, prices, kinds, strict=False):
        resolved_timestamp = _safe_int(timestamp_ms)
        resolved_price = _safe_float(price)
        resolved_kind = str(kind or "").upper()
        if resolved_timestamp is None or resolved_price is None or resolved_kind not in {"H", "L"}:
            continue
        points.append((resolved_timestamp, float(resolved_price), resolved_kind))
    return points


def _draw_pno_structure_overlay(ax: plt.Axes, *, timestamps: np.ndarray, row: dict[str, object]) -> None:
    points = _resolve_pno_structure_points(row)
    if not points or timestamps.size == 0:
        return
    x_values = np.array(
        [_resolve_pno_timestamp_plot_idx(timestamps, int(timestamp_ms)) for timestamp_ms, _price, _kind in points],
        dtype=np.float64,
    )
    y_values = np.array([float(price) for _timestamp_ms, price, _kind in points], dtype=np.float64)
    ax.plot(
        x_values,
        y_values,
        color=_PNO_PLOT_STRUCTURE_LINE,
        linewidth=0.9,
        alpha=0.20,
        zorder=4.55,
    )


def _resolve_pno_bos_level_from_row(row: dict[str, object], points: list[tuple[int, float, str]]) -> tuple[int, float] | None:
    level_timestamp_ms = _safe_int(row.get("level_first_local_high_timestamp_ms")) or _safe_int(row.get("structure_high_timestamp_ms"))
    if level_timestamp_ms is not None:
        matching_highs = [
            (timestamp_ms, price)
            for timestamp_ms, price, kind in points
            if kind == "H" and int(timestamp_ms) == int(level_timestamp_ms)
        ]
        if matching_highs:
            timestamp_ms, price = matching_highs[-1]
            return int(timestamp_ms), float(price)
        level_price = _safe_float(row.get("level")) or _safe_float(row.get("structure_high"))
        if level_price is not None:
            return int(level_timestamp_ms), float(level_price)
    if len(points) < 3:
        return None
    last_low_pos = -1
    for pos in range(len(points) - 1, -1, -1):
        if points[pos][2] == "L":
            last_low_pos = pos
            break
    if last_low_pos <= 0:
        return None
    for pos in range(last_low_pos - 1, -1, -1):
        if points[pos][2] == "H":
            return int(points[pos][0]), float(points[pos][1])
    return None


def _simplify_pno_structure_points_for_plot(
    points: list[tuple[int, float, str]],
    *,
    preserve_timestamp_ms: int | None = None,
) -> list[tuple[int, float, str]]:
    if len(points) <= 5:
        return points
    preserve_indices: set[int] = {0, len(points) - 1}
    if preserve_timestamp_ms is not None:
        preserve_indices.add(
            min(
                range(len(points)),
                key=lambda index: abs(int(points[index][0]) - int(preserve_timestamp_ms)),
            )
        )
    reduced_points = list(points)
    reduced_preserve_timestamps = {int(points[index][0]) for index in preserve_indices}

    def _merge_same_kind_neighbors() -> None:
        pos = 1
        while pos < len(reduced_points):
            if reduced_points[pos][2] != reduced_points[pos - 1][2]:
                pos += 1
                continue
            left = reduced_points[pos - 1]
            right = reduced_points[pos]
            if int(left[0]) in reduced_preserve_timestamps:
                remove_pos = pos
            elif int(right[0]) in reduced_preserve_timestamps:
                remove_pos = pos - 1
            elif left[2] == "H":
                remove_pos = pos - 1 if float(right[1]) >= float(left[1]) else pos
            else:
                remove_pos = pos - 1 if float(right[1]) <= float(left[1]) else pos
            reduced_points.pop(remove_pos)
            pos = max(pos - 1, 1)

    prices_for_reduction = np.array([float(price) for _timestamp_ms, price, _kind in reduced_points], dtype=np.float64)
    price_span_for_reduction = float(np.nanmax(prices_for_reduction) - np.nanmin(prices_for_reduction))
    if np.isfinite(price_span_for_reduction) and price_span_for_reduction > 0.0:
        min_visible_swing = price_span_for_reduction * 0.12
        while len(reduced_points) > 6:
            weakest_index = -1
            weakest_swing = np.inf
            for index in range(1, len(reduced_points) - 1):
                if int(reduced_points[index][0]) in reduced_preserve_timestamps:
                    continue
                swing = min(
                    abs(float(reduced_points[index][1]) - float(reduced_points[index - 1][1])),
                    abs(float(reduced_points[index + 1][1]) - float(reduced_points[index][1])),
                )
                if swing < weakest_swing:
                    weakest_swing = swing
                    weakest_index = index
            if weakest_index < 0 or weakest_swing >= min_visible_swing:
                break
            reduced_points.pop(weakest_index)
            _merge_same_kind_neighbors()
    points = reduced_points
    if len(points) <= 5:
        return points
    timestamps = np.array([float(timestamp_ms) for timestamp_ms, _price, _kind in points], dtype=np.float64)
    prices = np.array([float(price) for _timestamp_ms, price, _kind in points], dtype=np.float64)
    x_span = float(np.nanmax(timestamps) - np.nanmin(timestamps))
    y_span = float(np.nanmax(prices) - np.nanmin(prices))
    if not np.isfinite(x_span) or not np.isfinite(y_span) or x_span <= 0.0 or y_span <= 0.0:
        return points
    normalized = np.column_stack(((timestamps - float(np.nanmin(timestamps))) / x_span, (prices - float(np.nanmin(prices))) / y_span))
    keep_indices: set[int] = {0, len(points) - 1}
    if preserve_timestamp_ms is not None:
        keep_indices.add(
            min(
                range(len(points)),
                key=lambda index: abs(int(points[index][0]) - int(preserve_timestamp_ms)),
            )
        )

    def _mark_segment(start: int, end: int) -> None:
        if end <= start + 1:
            return
        segment_start = normalized[start]
        segment_end = normalized[end]
        segment = segment_end - segment_start
        segment_norm = float(np.linalg.norm(segment))
        max_distance = -1.0
        max_index = -1
        for index in range(start + 1, end):
            point = normalized[index]
            if segment_norm <= 1e-12:
                distance = float(np.linalg.norm(point - segment_start))
            else:
                delta = point - segment_start
                distance = float(abs((segment[0] * delta[1]) - (segment[1] * delta[0])) / segment_norm)
            if distance > max_distance:
                max_distance = distance
                max_index = index
        if max_index >= 0 and (max_distance >= 0.14 or max_index in keep_indices):
            keep_indices.add(max_index)
            _mark_segment(start, max_index)
            _mark_segment(max_index, end)

    _mark_segment(0, len(points) - 1)
    changed = True
    while changed:
        changed = False
        ordered_indices = sorted(keep_indices)
        for left, right in zip(ordered_indices, ordered_indices[1:], strict=False):
            if points[left][2] != points[right][2]:
                continue
            opposite_kind = "L" if points[left][2] == "H" else "H"
            candidates = [index for index in range(left + 1, right) if points[index][2] == opposite_kind]
            if not candidates:
                continue
            if opposite_kind == "L":
                bridge_index = min(candidates, key=lambda index: float(points[index][1]))
            else:
                bridge_index = max(candidates, key=lambda index: float(points[index][1]))
            keep_indices.add(bridge_index)
            changed = True
            break
    return [points[index] for index in sorted(keep_indices)]


def _draw_pno_structure_inset(ax: plt.Axes, *, plot_frame: pd.DataFrame, row: dict[str, object]) -> None:
    points = _resolve_pno_structure_points(row)
    if len(points) < 3 or plot_frame.empty:
        return
    active_high_timestamp_ms = _safe_int(row.get("active_high_timestamp_ms"))
    end_timestamp_ms = (
        _safe_int(row.get("structure_break_timestamp_ms"))
        or _safe_int(row.get("entry_signal_timestamp_ms"))
        or _safe_int(row.get("entry_timestamp_ms"))
    )
    if active_high_timestamp_ms is None or end_timestamp_ms is None or end_timestamp_ms <= active_high_timestamp_ms:
        return
    timestamps = plot_frame["timestamp"].to_numpy(dtype=np.int64)
    start_idx = int(np.searchsorted(timestamps, active_high_timestamp_ms, side="left"))
    end_idx = int(np.searchsorted(timestamps, end_timestamp_ms, side="right") - 1)
    start_idx = min(max(start_idx, 0), len(plot_frame) - 1)
    end_idx = min(max(end_idx, start_idx), len(plot_frame) - 1)
    if end_idx - start_idx < 2:
        return

    inset = ax.inset_axes([0.02, 0.56, 0.35, 0.42], transform=ax.transAxes)
    inset.set_facecolor(_PNO_PLOT_AXIS_FACE)
    for spine in inset.spines.values():
        spine.set_color(_PNO_PLOT_TEXT)
        spine.set_linewidth(0.7)
        spine.set_alpha(0.85)
    inset.set_xticks([])
    inset.set_yticks([])
    inset.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

    inset_frame = plot_frame.iloc[start_idx : end_idx + 1].copy()
    x_values = np.arange(len(inset_frame), dtype=np.float64)
    _draw_pno_candles(
        inset,
        inset_frame,
        x_values,
        up_color="#94a3b8",
        down_color="#94a3b8",
        wick_linewidth=0.75,
        body_linewidth=0.45,
        alpha=0.82,
        zorder=2,
    )

    inset_timestamps = inset_frame["timestamp"].to_numpy(dtype=np.int64)
    structure_points = [
        (timestamp_ms, price, kind)
        for timestamp_ms, price, kind in points
        if int(inset_timestamps[0]) <= int(timestamp_ms) <= int(inset_timestamps[-1])
    ]
    bos_level = _resolve_pno_bos_level_from_row(row, points)
    structure_points = _simplify_pno_structure_points_for_plot(
        structure_points,
        preserve_timestamp_ms=bos_level[0] if bos_level is not None else None,
    )
    if len(structure_points) >= 2:
        structure_x = np.array(
            [
                _resolve_pno_timestamp_plot_idx(inset_timestamps, int(timestamp_ms))
                for timestamp_ms, _price, _kind in structure_points
            ],
            dtype=np.float64,
        )
        structure_y = np.array([float(price) for _timestamp_ms, price, _kind in structure_points], dtype=np.float64)
        inset.plot(
            structure_x,
            structure_y,
            color=_PNO_PLOT_STRUCTURE_INSET_LINE,
            linewidth=2.0,
            alpha=0.95,
            zorder=5,
        )

    if bos_level is not None:
        level_timestamp_ms, level_price = bos_level
        level_x = _resolve_pno_timestamp_plot_idx(inset_timestamps, level_timestamp_ms)
        inset.hlines(
            level_price,
            max(float(level_x) - 0.35, -0.5),
            len(inset_frame) - 0.5,
            colors=_PNO_PLOT_STRUCTURE_INSET_BOS,
            linewidth=1.8,
            alpha=0.92,
            zorder=4.8,
        )

    low = float(np.nanmin(inset_frame["low"].to_numpy(dtype=np.float64)))
    high = float(np.nanmax(inset_frame["high"].to_numpy(dtype=np.float64)))
    if np.isfinite(low) and np.isfinite(high) and high > low:
        padding = max((high - low) * 0.08, 1e-9)
        inset.set_ylim(low - padding, high + padding)
    inset.set_xlim(-0.5, len(inset_frame) - 0.5)


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
    resolved_width = _resolve_pno_candle_width(x_values, default=candle_width)
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
            (float(x_pos) - resolved_width / 2.0, float(body_low)),
            resolved_width,
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
    deduped: list[int] = []
    seen_labels: set[str] = set()
    for pos in sorted(set(selected)):
        if pos < 0 or pos >= len(timestamp_index):
            continue
        ts = timestamp_index[pos]
        label = ts.strftime("%m-%d") if ts.hour == 0 and ts.minute == 0 else ts.strftime("%H:%M")
        if label in seen_labels:
            continue
        seen_labels.add(label)
        deduped.append(int(pos))
    return np.asarray(deduped, dtype=int)


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


def _resolve_pno_trade_chart_entry_visuals(
    trade_row: dict[str, object],
) -> tuple[float | None, float | None, int | None]:
    entry_confirmation_mode = str(trade_row.get("entry_confirmation_mode") or "")
    entry_signal_timestamp_ms = _safe_int(trade_row.get("entry_signal_timestamp_ms"))
    entry_plan_price = _safe_float(trade_row.get("entry_plan"))
    level_price = _safe_float(trade_row.get("level"))
    execution_price = _safe_float(trade_row.get("entry_price"))
    if execution_price is None:
        execution_price = _safe_float(trade_row.get("entry_price_actual"))

    display_entry_price = execution_price
    execution_tag_price: float | None = None
    if entry_confirmation_mode == "cross":
        display_entry_price = level_price or entry_plan_price or execution_price
    elif entry_confirmation_mode == "close_above":
        display_entry_price = entry_plan_price or level_price or execution_price
        if not _pno_prices_close(display_entry_price, execution_price):
            execution_tag_price = execution_price

    return display_entry_price, execution_tag_price, entry_signal_timestamp_ms


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
    entry_price = _safe_float(trade_row.get("entry_price"))
    if entry_price is None:
        entry_price = _safe_float(trade_row.get("entry_price_actual"))
    stop_loss = _safe_float(trade_row.get("sl_plan"))
    if stop_loss is None:
        stop_loss = _safe_float(trade_row.get("sl_actual"))
    initial_stop_loss = _safe_float(trade_row.get("initial_stop_loss")) or stop_loss
    tp1 = _safe_float(trade_row.get("tp1"))
    tp2 = _safe_float(trade_row.get("tp2"))
    runner_stop_after_tp1 = _safe_float(trade_row.get("runner_stop_after_tp1"))
    runner_stop_after_tp1_timestamp_ms = _safe_int(trade_row.get("runner_stop_after_tp1_timestamp_ms"))
    be_protect_price = _safe_float(trade_row.get("be_protect_price"))
    be_arm_timestamp_ms = _safe_int(trade_row.get("be_arm_timestamp_ms"))
    tp1_hit_timestamp_ms = _safe_int(trade_row.get("tp1_hit_timestamp_ms"))
    tp2_hit_timestamp_ms = _safe_int(trade_row.get("tp2_hit_timestamp_ms"))
    partial_exit_timestamp_ms = _safe_int(trade_row.get("partial_exit_timestamp_ms")) or tp1_hit_timestamp_ms
    partial_exit_price = _safe_float(trade_row.get("partial_exit_price")) or tp1
    runner_exit_price = _safe_float(trade_row.get("runner_exit_price"))
    pump_start_timestamp_ms = _safe_int(trade_row.get("pump_start_timestamp_ms")) or entry_timestamp_ms
    level_price = _safe_float(trade_row.get("level"))
    level_first_timestamp_ms = _safe_int(trade_row.get("level_first_local_high_timestamp_ms"))
    bos_level = _resolve_pno_bos_level_from_row(trade_row, _resolve_pno_structure_points(trade_row))
    if bos_level is not None:
        level_first_timestamp_ms, level_price = bos_level
    active_high = _safe_float(trade_row.get("active_high"))
    pullback_low = _safe_float(trade_row.get("pullback_low"))
    active_high_timestamp_ms = _safe_int(trade_row.get("active_high_timestamp_ms"))
    pullback_low_timestamp_ms = _safe_int(trade_row.get("pullback_low_timestamp_ms"))
    pullback_base_low = _safe_float(trade_row.get("pullback_base_low"))
    pullback_base_high = _safe_float(trade_row.get("pullback_base_high"))
    pullback_base_start_timestamp_ms = _safe_int(trade_row.get("pullback_base_start_timestamp_ms"))
    pullback_base_end_timestamp_ms = _safe_int(trade_row.get("pullback_base_end_timestamp_ms"))
    if (
        entry_timestamp_ms is None
        or exit_timestamp_ms is None
        or entry_price is None
        or stop_loss is None
        or initial_stop_loss is None
        or pump_start_timestamp_ms is None
    ):
        return None
    display_entry_price, execution_tag_price, entry_signal_timestamp_ms = _resolve_pno_trade_chart_entry_visuals(trade_row)
    risk_entry_price = entry_price

    category = str(trade_row.get("category") or "")
    result_type = str(trade_row.get("result_type") or "")
    exit_price_actual = (
        _safe_float(trade_row.get("runner_exit_price"))
        or _safe_float(trade_row.get("exit_price_actual"))
        or _safe_float(trade_row.get("exit_price"))
    )
    show_high_tag = not _pno_prices_close(active_high, tp1)
    show_pullback_low_tag = not _pno_prices_close(pullback_low, initial_stop_loss)

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
    levels_window_columns = [
        column
        for column in ("timestamp", "open", "high", "low", "close", "volume", *_PNO_PLOT_TRADE_COUNT_COLUMNS)
        if column in levels_frame.columns
    ]
    levels_window = levels_frame.loc[
        (levels_frame["timestamp"] >= window_start_ms)
        & (levels_frame["timestamp"] <= window_end_ms),
        levels_window_columns,
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
    signal_idx = _resolve_pno_timestamp_plot_idx(timestamps, entry_signal_timestamp_ms) if entry_signal_timestamp_ms is not None else None
    be_arm_idx = _resolve_pno_timestamp_plot_idx(timestamps, be_arm_timestamp_ms) if be_arm_timestamp_ms is not None else None
    tp1_hit_idx = _resolve_pno_timestamp_plot_idx(timestamps, tp1_hit_timestamp_ms) if tp1_hit_timestamp_ms is not None else None
    tp2_hit_idx = _resolve_pno_timestamp_plot_idx(timestamps, tp2_hit_timestamp_ms) if tp2_hit_timestamp_ms is not None else None
    runner_stop_after_tp1_idx = (
        _resolve_pno_timestamp_plot_idx(timestamps, runner_stop_after_tp1_timestamp_ms)
        if runner_stop_after_tp1_timestamp_ms is not None
        else None
    )
    pump_idx = (
        _resolve_pno_timestamp_plot_idx(timestamps, int(pump_start_timestamp_ms))
        if pump_start_timestamp_ms is not None
        else None
    )
    entry_idx = min(max(entry_idx, 0), len(plot_frame) - 1)
    exit_idx = min(max(exit_idx, entry_idx), len(plot_frame) - 1)
    if signal_idx is not None:
        signal_idx = min(max(signal_idx, 0), entry_idx)
    if pump_idx is None:
        pump_idx = entry_idx
    price_axis_right_x = float(len(plot_frame) - 0.5)

    fig, (ax_price, ax_levels, ax_volume, ax_trades) = plt.subplots(
        4,
        1,
        figsize=_PNO_TRADE_CHART_FIGSIZE,
        dpi=100,
        sharex=True,
        gridspec_kw={"height_ratios": _PNO_TRADE_CHART_HEIGHT_RATIOS, "hspace": 0.05},
        facecolor=_PNO_PLOT_FIGURE_FACE,
    )
    _configure_pno_plot_axes(price_ax=ax_price, volume_ax=ax_volume, trades_ax=ax_trades)
    _configure_pno_plot_axes(price_ax=ax_levels, volume_ax=ax_volume, trades_ax=ax_trades)

    _draw_pno_candles(ax_price, plot_frame, x_values)
    _draw_pno_structure_inset(ax_price, plot_frame=plot_frame, row=trade_row)
    ax_price.set_title(_format_pno_chart_symbol(symbol), loc="left", color=_PNO_PLOT_TEXT, fontsize=11, pad=10, fontweight="semibold")
    if not levels_window.empty:
        _draw_pno_candles_on_columns(ax_levels, levels_window, x_column="plot_x", candle_width=_PNO_PLOT_5M_CANDLE_WIDTH)
    ax_price.plot(x_values, ema9, color=_PNO_PLOT_EMA9, linewidth=1.2, alpha=0.28, zorder=2.2)
    ax_price.plot(x_values, ema20, color=_PNO_PLOT_EMA20, linewidth=1.2, alpha=0.24, zorder=2.1)
    ax_price.axvline(pump_idx, color=_PNO_PLOT_PUMP, linewidth=0.95, alpha=0.26, zorder=5)
    _draw_pno_pump_pause_zones(
        ax_price,
        timestamps=timestamps,
        plot_frame=plot_frame,
        pump_start_timestamp_ms=pump_start_timestamp_ms,
        active_high_timestamp_ms=active_high_timestamp_ms,
        alpha=0.10,
        zorder=2.05,
    )
    _draw_pno_pump_pause_zones(
        ax_levels,
        timestamps=timestamps,
        plot_frame=plot_frame,
        pump_start_timestamp_ms=pump_start_timestamp_ms,
        active_high_timestamp_ms=active_high_timestamp_ms,
        alpha=0.08,
        zorder=2.05,
    )

    def _add_trade_block(
        *,
        start_idx: int | None,
        end_idx: int | None,
        lower_price: float | None,
        upper_price: float | None,
        facecolor: str,
        edgecolor: str,
        alpha: float,
        zorder: float,
    ) -> None:
        if start_idx is None or end_idx is None or lower_price is None or upper_price is None:
            return
        block_start = min(max(int(start_idx), 0), len(plot_frame) - 1)
        block_end = min(max(int(end_idx), block_start), len(plot_frame) - 1)
        price_low = min(float(lower_price), float(upper_price))
        price_high = max(float(lower_price), float(upper_price))
        ax_price.add_patch(
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

    tp1_label_price = partial_exit_price or tp1
    tp2_label_price = tp2
    be_label_price = be_protect_price if be_arm_idx is not None else None
    tag_positions = _resolve_pno_axis_tag_positions(
        [
             ("TP2", tp2_label_price),
             ("TP1", tp1_label_price),
             ("High", active_high if show_high_tag else None),
             ("BE", be_label_price),
             ("Entry", display_entry_price),
             ("Exec", execution_tag_price),
            ("Level", level_price),
            ("SL", initial_stop_loss),
            ("Exit", exit_price_actual),
        ]
    )
    if pullback_base_low is not None and pullback_base_high is not None:
        _draw_pno_price_zone(
            ax_price,
            timestamps=timestamps,
            start_timestamp_ms=pullback_base_start_timestamp_ms,
            end_timestamp_ms=pullback_base_end_timestamp_ms or entry_timestamp_ms,
            low=pullback_base_low,
            high=pullback_base_high,
            facecolor=_PNO_PLOT_BASE_FACE,
            edgecolor=_PNO_PLOT_BASE_EDGE,
            alpha=0.12,
            linewidth=0.95,
            zorder=2.35,
        )
        _draw_pno_price_zone(
            ax_levels,
            timestamps=timestamps,
            start_timestamp_ms=pullback_base_start_timestamp_ms,
            end_timestamp_ms=pullback_base_end_timestamp_ms or entry_timestamp_ms,
            low=pullback_base_low,
            high=pullback_base_high,
            facecolor=_PNO_PLOT_BASE_FACE,
            edgecolor=_PNO_PLOT_BASE_EDGE,
            alpha=0.10,
            linewidth=0.85,
            zorder=2.2,
        )
    if level_price is not None:
        level_end_idx = signal_idx if signal_idx is not None else entry_idx
        level_start_idx = level_end_idx
        if level_first_timestamp_ms is not None:
            level_start_idx = int(np.searchsorted(timestamps, level_first_timestamp_ms, side="left"))
            level_start_idx = min(max(level_start_idx, 0), level_end_idx)
        ax_price.hlines(
            level_price,
            level_start_idx - 0.48,
            level_end_idx + 0.48,
            colors=_PNO_PLOT_LEVEL,
            linewidth=0.95,
            alpha=0.9,
            zorder=5,
        )
        ax_levels.hlines(
            level_price,
            level_start_idx - 0.48,
            level_end_idx + 0.48,
            colors=_PNO_PLOT_LEVEL,
            linewidth=0.85,
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
            end_timestamp_ms=entry_signal_timestamp_ms or entry_timestamp_ms,
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
    if entry_confirmation_mode == "close_above" and signal_idx is not None and signal_idx != entry_idx:
        ax_price.axvline(signal_idx, color=_PNO_PLOT_ENTRY, linewidth=0.9, alpha=0.18, linestyle="--", zorder=4.8)
    initial_phase_end_idx = exit_idx
    for candidate_idx in (be_arm_idx, tp1_hit_idx, tp2_hit_idx):
        if candidate_idx is not None:
            initial_phase_end_idx = min(initial_phase_end_idx, int(candidate_idx))
    _add_trade_block(
        start_idx=entry_idx,
        end_idx=initial_phase_end_idx,
        lower_price=initial_stop_loss,
        upper_price=risk_entry_price,
        facecolor=_PNO_PLOT_RISK_FACE,
        edgecolor=_PNO_PLOT_RISK_EDGE,
        alpha=0.30,
        zorder=1.05,
    )
    if tp1_label_price is not None:
        _add_trade_block(
            start_idx=entry_idx,
            end_idx=tp1_hit_idx if tp1_hit_idx is not None else initial_phase_end_idx,
            lower_price=risk_entry_price,
            upper_price=tp1_label_price,
            facecolor=_PNO_PLOT_PROFIT_FACE,
            edgecolor=_PNO_PLOT_PROFIT_EDGE,
            alpha=0.24,
            zorder=1.08,
        )
    if exit_price_actual is not None:
        ax_price.hlines(
            exit_price_actual,
            exit_idx - 0.48,
            len(plot_frame) - 0.5,
            colors=_PNO_PLOT_EXIT,
            linewidth=1.0,
            alpha=0.88,
            linestyle="-.",
            zorder=5.25,
        )
    if be_arm_idx is not None and be_protect_price is not None and exit_idx >= be_arm_idx:
        _add_trade_block(
            start_idx=be_arm_idx,
            end_idx=exit_idx,
            lower_price=risk_entry_price,
            upper_price=be_protect_price,
            facecolor=_PNO_PLOT_PROTECT_FACE,
            edgecolor=_PNO_PLOT_PROTECT_EDGE,
            alpha=0.18,
            zorder=1.07,
        )
    if tp2_hit_idx is not None and tp2_label_price is not None:
        runner_floor = be_protect_price if be_protect_price is not None else risk_entry_price
        _add_trade_block(
            start_idx=tp1_hit_idx or be_arm_idx or entry_idx,
            end_idx=tp2_hit_idx,
            lower_price=runner_floor,
            upper_price=tp2_label_price,
            facecolor=_PNO_PLOT_PROFIT_FACE,
            edgecolor=_PNO_PLOT_PROFIT_EDGE,
            alpha=0.28,
            zorder=1.1,
        )
    if tp1_label_price is not None:
        _annotate_pno_axis_price_tag(
            ax_price,
            y=tp1_label_price,
            label="TP1",
            color=_PNO_PLOT_PROFIT_EDGE,
            leader_start_x=float(entry_idx - 0.5),
            leader_end_x=price_axis_right_x,
            text_y=tag_positions.get("TP1"),
            alpha=0.92,
        )
    if tp2_label_price is not None and not _pno_prices_close(tp2_label_price, tp1_label_price):
        _annotate_pno_axis_price_tag(
            ax_price,
            y=tp2_label_price,
            label="TP2",
            color=_PNO_PLOT_PROFIT_EDGE,
            leader_start_x=float(tp1_hit_idx or entry_idx),
            leader_end_x=price_axis_right_x,
            text_y=tag_positions.get("TP2"),
            alpha=0.92,
        )
    if be_label_price is not None:
        _annotate_pno_axis_price_tag(
            ax_price,
            y=be_label_price,
            label="BE",
            color=_PNO_PLOT_PROTECT_EDGE,
            leader_start_x=float(be_arm_idx or entry_idx),
            leader_end_x=price_axis_right_x,
            text_y=tag_positions.get("BE"),
            alpha=0.90,
        )
    _annotate_pno_axis_price_tag(
        ax_price,
        y=initial_stop_loss,
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
        leader_start_x=float(signal_idx if signal_idx is not None else entry_idx),
        leader_end_x=price_axis_right_x,
        text_y=tag_positions.get("Entry"),
        alpha=0.9,
    )
    if execution_tag_price is not None:
        _annotate_pno_axis_price_tag(
            ax_price,
            y=execution_tag_price,
            label="Exec",
            color=_PNO_PLOT_ENTRY,
            leader_start_x=float(entry_idx),
            leader_end_x=price_axis_right_x,
            text_y=tag_positions.get("Exec"),
            alpha=0.82,
        )
    if exit_price_actual is not None:
        _annotate_pno_axis_price_tag(
            ax_price,
            y=exit_price_actual,
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
        levels_x = levels_window["plot_x"].to_numpy(dtype=np.float64)
        levels_volume_width = _resolve_pno_candle_width(levels_x, default=_PNO_PLOT_5M_CANDLE_WIDTH)
        levels_volume_max = float(np.nanmax(levels_volume)) if len(levels_volume) > 0 else 0.0
        levels_volume_pct = (levels_volume / levels_volume_max) * 100.0 if levels_volume_max > 0.0 else np.zeros_like(levels_volume)
        levels_volume_colors = np.where(levels_close >= levels_open, _PNO_PLOT_UP, _PNO_PLOT_DOWN)
        ax_volume.bar(
            levels_x,
            levels_volume_pct,
            width=levels_volume_width,
            color=levels_volume_colors,
            edgecolor="none",
            alpha=0.88,
            zorder=3,
        )

    trade_counts = _resolve_trade_count_series(plot_frame)
    trade_counts_max = float(np.nanmax(trade_counts)) if trade_counts.size else 0.0
    if trade_counts_max > 0.0:
        trade_counts_pct = (trade_counts / trade_counts_max) * 100.0
        trade_count_colors = np.where(closes >= opens, _PNO_PLOT_UP, _PNO_PLOT_DOWN)
        ax_trades.bar(
            x_values,
            trade_counts_pct,
            width=_resolve_pno_candle_width(x_values, default=0.82),
            color=trade_count_colors,
            edgecolor="none",
            alpha=0.78,
            zorder=3,
        )
    elif not levels_window.empty:
        levels_trades = _resolve_trade_count_series(levels_window)
        levels_trades_max = float(np.nanmax(levels_trades)) if levels_trades.size else 0.0
        if levels_trades_max > 0.0:
            levels_trades_pct = (levels_trades / levels_trades_max) * 100.0
            ax_trades.bar(
                levels_x,
                levels_trades_pct,
                width=levels_volume_width,
                color=levels_volume_colors,
                edgecolor="none",
                alpha=0.78,
                zorder=3,
            )

    padding = max((float(np.nanmax(high_values)) - float(np.nanmin(low_values))) * 0.05, 1e-9)
    ax_price.set_ylim(float(np.nanmin(low_values)) - padding, float(np.nanmax(high_values)) + padding)
    entry_candle_width = _resolve_pno_candle_width(x_values)
    x_left_pad = max(0.5, entry_candle_width * 0.65)
    x_right_pad = max(0.5, entry_candle_width * 0.65)
    ax_price.set_xlim(-x_left_pad, len(plot_frame) - 1 + x_right_pad)
    if not levels_window.empty:
        levels_high = levels_window["high"].to_numpy(dtype=np.float64)
        levels_low = levels_window["low"].to_numpy(dtype=np.float64)
        levels_padding = max((float(np.nanmax(levels_high)) - float(np.nanmin(levels_low))) * 0.08, 1e-9)
        ax_levels.set_ylim(float(np.nanmin(levels_low)) - levels_padding, float(np.nanmax(levels_high)) + levels_padding)
    ax_levels.set_xlim(-x_left_pad, len(plot_frame) - 1 + x_right_pad)
    ax_price.set_ylabel(_format_pno_timeframe_label(_infer_pno_frame_step_ms(plot_frame, default_ms=60_000)))
    ax_levels.set_ylabel(_format_pno_timeframe_label(_infer_pno_frame_step_ms(levels_window, default_ms=5 * 60_000)))
    ax_volume.set_ylabel("Vol %")
    ax_trades.set_ylabel("Trades %")
    ax_levels.yaxis.set_label_coords(-0.072, 0.5)
    ax_volume.set_ylim(0.0, 100.0)
    ax_volume.set_yticks([0.0, 50.0, 100.0])
    ax_volume.set_yticklabels(["0", "50", "100"], color=_PNO_PLOT_MUTED)
    ax_trades.set_ylim(0.0, 100.0)
    ax_trades.set_yticks([0.0, 50.0, 100.0])
    ax_trades.set_yticklabels(["0", "50", "100"], color=_PNO_PLOT_MUTED)
    tick_timestamps = _build_pno_tick_timestamps(plot_frame)
    tick_positions = _build_pno_tick_positions_from_timestamps(tick_timestamps, len(plot_frame))
    tick_labels = _build_pno_tick_labels_from_timestamps(tick_timestamps, tick_positions)
    ax_trades.set_xticks(tick_positions)
    ax_trades.set_xticklabels(tick_labels)
    ax_price.tick_params(axis="x", labelbottom=False)
    ax_levels.tick_params(axis="x", labelbottom=False)
    ax_volume.tick_params(axis="x", labelbottom=False)
    ax_levels.margins(x=0.0)
    ax_price.margins(x=0.01)
    ax_volume.margins(x=0.0)
    ax_trades.margins(x=0.0)

    file_name = f"{_sanitize_plot_name(symbol.replace('/', '_'))}_{trade_index:03d}_{_sanitize_plot_name(result_type.lower() or category.lower() or 'trade')}.png"
    output_path = charts_dir / file_name
    fig.subplots_adjust(left=0.10, right=0.80, top=0.94, bottom=0.06, hspace=0.05)
    fig.savefig(output_path, **_PNO_PLOT_SAVEFIG_KWARGS)
    plt.close(fig)
    return output_path


def _render_pno_trade_charts_for_symbol(
    *,
    charts_dir: Path,
    symbol: str,
    mtf_frames: SymbolMtfFrames,
    trade_rows: list[dict[str, object]],
    seconds_frame_provider: object | None = None,
) -> list[str]:
    if not trade_rows:
        return []
    levels_plot_frame = _prepare_pno_levels_plot_source(mtf_frames.levels_frame)
    entry_source_frame = mtf_frames.entry_frame
    target_entry_ms = int(mtf_frames.entry_timeframe.to_milliseconds())
    source_entry_ms = _infer_pno_frame_step_ms(entry_source_frame, target_entry_ms)
    if source_entry_ms > target_entry_ms and seconds_frame_provider is not None:
        timestamps: list[int] = []
        for row in trade_rows:
            for key in (
                "pump_start_timestamp_ms",
                "active_high_timestamp_ms",
                "pullback_low_timestamp_ms",
                "structure_break_timestamp_ms",
                "entry_timestamp_ms",
                "exit_timestamp_ms",
            ):
                value = _safe_int(row.get(key))
                if value is not None:
                    timestamps.append(int(value))
        if timestamps:
            loader = getattr(seconds_frame_provider, "load_aggregated_window", None)
            if callable(loader):
                loaded = loader(
                    symbol=symbol,
                    start_timestamp_ms=max(min(timestamps) - 30 * 60_000, 0),
                    end_timestamp_ms=max(timestamps) + 30 * 60_000,
                    target_timeframe=mtf_frames.entry_timeframe,
                )
                if isinstance(loaded, pd.DataFrame) and not loaded.empty:
                    entry_source_frame = loaded
    entry_plot_frame = _prepare_pno_entry_plot_source_with_ema(
        levels_frame=levels_plot_frame,
        entry_frame=entry_source_frame,
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


def _resolve_pno_stage_review_start_timestamp(
    *,
    timestamp_ms: int,
    pump_start_timestamp_ms: int | None,
    sleep_start_timestamp_ms: int | None,
    levels_step_ms: int,
    entry_step_ms: int,
    include_long_sleep_context: bool,
) -> int:
    if pump_start_timestamp_ms is None:
        return max(timestamp_ms - (90 * 60_000), 0)
    if include_long_sleep_context and sleep_start_timestamp_ms is not None:
        target_start = int(pump_start_timestamp_ms) - (_PNO_STAGE_REVIEW_SLEEP_CONTEXT_BARS * levels_step_ms)
        return max(min(int(sleep_start_timestamp_ms), target_start), 0)
    if include_long_sleep_context:
        return max(
            int(pump_start_timestamp_ms) - (_PNO_STAGE_REVIEW_SLEEP_CONTEXT_BARS * levels_step_ms),
            0,
        )
    return max(
        int(pump_start_timestamp_ms)
        - min(_PNO_STAGE_REVIEW_MAX_ENTRY_LOOKBACK_MS, max(90 * 60_000, 96 * entry_step_ms)),
        0,
    )


def _resolve_pno_sleep_volume_baseline(
    *,
    levels_frame: pd.DataFrame,
    entry_frame: pd.DataFrame,
    plot_frame: pd.DataFrame,
    pump_start_timestamp_ms: int | None,
    sleep_start_timestamp_ms: int | None,
    use_levels_frame: bool,
) -> float | None:
    if pump_start_timestamp_ms is None or plot_frame.empty:
        return None
    source = levels_frame if use_levels_frame else entry_frame
    if source.empty or "timestamp" not in source.columns or "volume" not in source.columns:
        return None
    step_ms = _infer_pno_frame_step_ms(source, default_ms=5 * 60_000 if use_levels_frame else 60_000)
    del sleep_start_timestamp_ms
    start_timestamp_ms = max(0, int(pump_start_timestamp_ms) - _PNO_STAGE_REVIEW_SLEEP_BASELINE_MS)
    end_timestamp_ms = max(0, int(pump_start_timestamp_ms) - step_ms)
    sleep_window = source.loc[
        (source["timestamp"] >= start_timestamp_ms)
        & (source["timestamp"] <= end_timestamp_ms),
        "volume",
    ]
    sleep_volume = pd.to_numeric(sleep_window, errors="coerce").dropna()
    if sleep_volume.empty:
        return None
    baseline = float(sleep_volume.mean())
    return baseline if np.isfinite(baseline) and baseline > 0.0 else None


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
    levels_timeframe_ms: int | None = None,
    entry_timeframe_ms: int | None = None,
) -> str | None:
    stage_id = str(review_row.get("stage_id", "stage"))
    current_timestamp_ms = _safe_int(review_row.get("current_timestamp_ms"))
    timestamp_ms = current_timestamp_ms or _safe_int(review_row.get("timestamp_ms"))
    if timestamp_ms is None:
        return None
    pump_start_timestamp_ms = _safe_int(review_row.get("pump_start_timestamp_ms"))
    sleep_start_timestamp_ms = _safe_int(review_row.get("sleep_start_timestamp_ms"))
    active_high_timestamp_ms = _safe_int(review_row.get("active_high_timestamp_ms"))
    pullback_low_timestamp_ms = _safe_int(review_row.get("pullback_low_timestamp_ms"))
    level_first_timestamp_ms = _safe_int(review_row.get("level_first_local_high_timestamp_ms"))
    pullback_base_start_timestamp_ms = _safe_int(review_row.get("pullback_base_start_timestamp_ms"))
    is_stage1 = stage_id == PNO_STAGE_SEQUENCE[0]
    use_levels_frame = is_stage1
    levels_step_ms = int(levels_timeframe_ms or _infer_pno_frame_step_ms(levels_frame, default_ms=5 * 60_000))
    entry_step_ms = int(entry_timeframe_ms or _infer_pno_frame_step_ms(entry_frame, default_ms=60_000))
    if is_stage1 and pump_start_timestamp_ms is not None:
        start_timestamp_ms = _resolve_pno_stage_review_start_timestamp(
            timestamp_ms=int(timestamp_ms),
            pump_start_timestamp_ms=pump_start_timestamp_ms,
            sleep_start_timestamp_ms=sleep_start_timestamp_ms,
            levels_step_ms=levels_step_ms,
            entry_step_ms=entry_step_ms,
            include_long_sleep_context=True,
        )
        end_timestamp_ms = timestamp_ms
    else:
        frame_step_ms = _infer_pno_frame_step_ms(levels_frame if use_levels_frame else entry_frame, 5 * 60_000)
        sleep_context_start = _resolve_pno_stage_review_start_timestamp(
            timestamp_ms=int(timestamp_ms),
            pump_start_timestamp_ms=pump_start_timestamp_ms,
            sleep_start_timestamp_ms=sleep_start_timestamp_ms,
            levels_step_ms=levels_step_ms,
            entry_step_ms=entry_step_ms,
            include_long_sleep_context=False,
        )
        context_candidates = [
            timestamp_ms - (90 * 60_000),
            sleep_context_start,
            (active_high_timestamp_ms - (12 * frame_step_ms)) if active_high_timestamp_ms is not None else None,
            (pullback_low_timestamp_ms - (6 * frame_step_ms)) if pullback_low_timestamp_ms is not None else None,
            (level_first_timestamp_ms - (6 * frame_step_ms)) if level_first_timestamp_ms is not None else None,
            (pullback_base_start_timestamp_ms - (4 * frame_step_ms)) if pullback_base_start_timestamp_ms is not None else None,
        ]
        finite_candidates = [int(value) for value in context_candidates if value is not None]
        start_timestamp_ms = max(min(finite_candidates), 0) if finite_candidates else max(timestamp_ms - (90 * 60_000), 0)
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

    fig, (ax_price, ax_volume, ax_trades) = plt.subplots(
        3,
        1,
        figsize=_PNO_STAGE_REVIEW_FIGSIZE,
        dpi=72,
        sharex=True,
        gridspec_kw={"height_ratios": _PNO_STAGE_REVIEW_HEIGHT_RATIOS},
    )
    fig.patch.set_facecolor(_PNO_PLOT_FIGURE_FACE)
    _configure_pno_plot_axes(price_ax=ax_price, volume_ax=ax_volume, trades_ax=ax_trades)

    opens = plot_frame["open"].to_numpy(dtype=np.float64)
    close_values = plot_frame["close"].to_numpy(dtype=np.float64)
    high_values = plot_frame["high"].to_numpy(dtype=np.float64)
    low_values = plot_frame["low"].to_numpy(dtype=np.float64)
    volume_values = plot_frame["volume"].fillna(0.0).to_numpy(dtype=np.float64)
    trade_counts = _resolve_trade_count_series(plot_frame)
    sleep_volume_baseline = _resolve_pno_sleep_volume_baseline(
        levels_frame=levels_frame,
        entry_frame=entry_frame,
        plot_frame=plot_frame,
        pump_start_timestamp_ms=pump_start_timestamp_ms,
        sleep_start_timestamp_ms=sleep_start_timestamp_ms,
        use_levels_frame=use_levels_frame,
    )
    x = np.arange(len(plot_frame), dtype=np.float64)
    ema9 = plot_frame["ema9"].to_numpy(dtype=np.float64) if "ema9" in plot_frame.columns else np.full(len(plot_frame), np.nan, dtype=np.float64)
    ema20 = plot_frame["ema20"].to_numpy(dtype=np.float64) if "ema20" in plot_frame.columns else np.full(len(plot_frame), np.nan, dtype=np.float64)
    timestamps = plot_frame["timestamp"].to_numpy(dtype=np.int64)
    event_idx = _resolve_pno_timestamp_plot_idx(timestamps, int(timestamp_ms))
    pump_idx = (
        _resolve_pno_timestamp_plot_idx(timestamps, int(pump_start_timestamp_ms))
        if pump_start_timestamp_ms is not None
        else None
    )

    _draw_pno_candles(ax_price, plot_frame, x)
    if not is_stage1:
        _draw_pno_structure_overlay(ax_price, timestamps=timestamps, row=review_row)
    if (not is_stage1) and np.isfinite(ema9).any():
        ax_price.plot(x, ema9, color=_PNO_PLOT_EMA9, linewidth=0.8, alpha=0.22, zorder=2)
    if (not is_stage1) and np.isfinite(ema20).any():
        ax_price.plot(x, ema20, color=_PNO_PLOT_EMA20, linewidth=0.8, alpha=0.20, zorder=2)
    if pump_idx is not None:
        ax_price.axvline(pump_idx, color=_PNO_PLOT_PUMP, linewidth=0.9, alpha=0.24, zorder=5)

    if not is_stage1:
        active_high = _safe_float(review_row.get("active_high"))
        pullback_low = _safe_float(review_row.get("pullback_low"))
        level_price = _safe_float(review_row.get("level"))
        entry_price = _safe_float(review_row.get("entry_price"))
        stage1_hold_price = _safe_float(review_row.get("stage1_hold_price"))
        level_last_timestamp_ms = _safe_int(review_row.get("level_valid_timestamp_ms")) or timestamp_ms
        pullback_base_low = _safe_float(review_row.get("pullback_base_low"))
        pullback_base_high = _safe_float(review_row.get("pullback_base_high"))
        pullback_base_end_timestamp_ms = _safe_int(review_row.get("pullback_base_end_timestamp_ms")) or timestamp_ms
        event_timestamp_ms = int(timestamp_ms)
        right_axis_x = float(ax_price.get_xlim()[1])
        label_positions = _resolve_pno_axis_tag_positions(
            [
                ("High", active_high),
                ("PB Low", pullback_low),
                ("Level", level_price),
                ("Entry", entry_price),
            ]
        )

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
            _annotate_pno_axis_price_tag(
                ax_price,
                y=active_high,
                label="High",
                color="#ef4444",
                leader_start_x=float(active_high_idx),
                leader_end_x=right_axis_x,
                text_y=label_positions.get("High"),
                alpha=0.92,
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
            _annotate_pno_axis_price_tag(
                ax_price,
                y=pullback_low,
                label="PB Low",
                color="#38bdf8",
                leader_start_x=float(pullback_low_idx),
                leader_end_x=right_axis_x,
                text_y=label_positions.get("PB Low"),
                alpha=0.92,
            )
        _draw_pno_price_zone(
            ax_price,
            timestamps=timestamps,
            start_timestamp_ms=pullback_base_start_timestamp_ms,
            end_timestamp_ms=pullback_base_end_timestamp_ms,
            low=pullback_base_low,
            high=pullback_base_high,
            facecolor=_PNO_PLOT_BASE_FACE,
            edgecolor=_PNO_PLOT_BASE_EDGE,
            alpha=0.12,
            linewidth=0.9,
            zorder=2.35,
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
        level_leader_start_x = None
        if level_first_timestamp_ms is not None:
            level_leader_start_x = float(
                min(
                    max(int(np.searchsorted(timestamps, int(level_first_timestamp_ms), side="left")), 0),
                    len(plot_frame) - 1,
                )
            )
        _annotate_pno_axis_price_tag(
            ax_price,
            y=level_price,
            label="Level",
            color=_PNO_PLOT_LEVEL,
            leader_start_x=level_leader_start_x,
            leader_end_x=right_axis_x,
            text_y=label_positions.get("Level"),
            alpha=0.90,
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
        _annotate_pno_axis_price_tag(
            ax_price,
            y=entry_price,
            label="Entry",
            color=_PNO_PLOT_ENTRY,
            leader_start_x=float(event_idx),
            leader_end_x=right_axis_x,
            text_y=label_positions.get("Entry"),
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
        ax_volume.bar(
            x,
            volume_pct,
            width=_resolve_pno_candle_width(x, default=0.82),
            color=volume_colors,
            edgecolor="none",
            alpha=0.8,
            zorder=2,
        )
        if sleep_volume_baseline is not None and volume_max > 0.0:
            baseline_pct = min(max((float(sleep_volume_baseline) / volume_max) * 100.0, 0.0), 100.0)
            ax_volume.axhline(
                baseline_pct,
                color=_PNO_STAGE_REVIEW_BASELINE_COLOR,
                linewidth=0.9,
                alpha=0.72,
                linestyle="--",
                zorder=3,
            )
        trades_max = float(np.nanmax(trade_counts)) if trade_counts.size and np.isfinite(trade_counts).any() else 0.0
        trades_pct = (trade_counts / trades_max) * 100.0 if trades_max > 0.0 else np.zeros_like(trade_counts)
        ax_trades.bar(
            x,
            trades_pct,
            width=_resolve_pno_candle_width(x, default=0.82),
            color=volume_colors,
            edgecolor="none",
            alpha=0.72,
            zorder=2,
        )

    if np.isfinite(high_values).any() and np.isfinite(low_values).any():
        padding = max((float(np.nanmax(high_values)) - float(np.nanmin(low_values))) * 0.06, 1e-9)
        ax_price.set_ylim(float(np.nanmin(low_values)) - padding, float(np.nanmax(high_values)) + padding)
    review_candle_width = _resolve_pno_candle_width(x)
    review_x_pad = max(0.5, review_candle_width * 0.65)
    ax_price.set_xlim(-review_x_pad, len(plot_frame) - 1 + review_x_pad)
    ax_volume.set_ylim(0.0, 100.0)
    ax_volume.set_yticks([0.0, 50.0, 100.0])
    ax_volume.set_yticklabels(["0", "50", "100"], color=_PNO_PLOT_MUTED)
    ax_trades.set_ylim(0.0, 100.0)
    ax_trades.set_yticks([0.0, 50.0, 100.0])
    ax_trades.set_yticklabels(["0", "50", "100"], color=_PNO_PLOT_MUTED)
    ax_price.set_ylabel(
        _format_pno_timeframe_label(levels_step_ms if use_levels_frame else entry_step_ms),
        color=_PNO_PLOT_MUTED,
        fontsize=8,
    )
    ax_volume.set_ylabel("Vol %", color=_PNO_PLOT_MUTED, fontsize=8)
    ax_trades.set_ylabel("Trades %", color=_PNO_PLOT_MUTED, fontsize=8)

    tick_timestamps = _build_pno_tick_timestamps(plot_frame)
    tick_positions = _build_pno_tick_positions_from_timestamps(tick_timestamps, len(plot_frame))
    tick_labels = _build_pno_tick_labels_from_timestamps(tick_timestamps, tick_positions)
    ax_trades.set_xticks(tick_positions)
    ax_trades.set_xticklabels(tick_labels, fontsize=8, color=_PNO_PLOT_MUTED)
    ax_price.tick_params(axis="x", labelbottom=False)
    ax_volume.tick_params(axis="x", labelbottom=False)

    sanitized_status = _sanitize_plot_name(status)
    sanitized_reason = _sanitize_plot_name(reason if reason is not None else str(review_row.get("reason", "ok")))
    file_name = f"{_sanitize_plot_name(symbol.replace('/', '_'))}_{review_index:04d}_{_sanitize_plot_name(stage_id)}_{sanitized_status}_{sanitized_reason}.png"
    output_path = charts_dir / file_name
    fig.subplots_adjust(left=0.08, right=0.80, top=0.985, bottom=0.10, hspace=0.04)
    fig.savefig(output_path, **_PNO_STAGE_REVIEW_SAVEFIG_KWARGS)
    plt.close(fig)
    return str(output_path)


def _export_pno_stage_reviews(
    *,
    diagnostics_dir: Path,
    symbol_frames: dict[str, SymbolMtfFrames],
    stage_rows_by_stage: dict[str, list[dict[str, object]]],
    stage_rejections_by_stage: dict[str, dict[str, list[dict[str, object]]]],
    selected_stage_ids: tuple[str, ...],
    render_charts: bool = True,
    passed_chart_stage_ids: tuple[str, ...] | None = None,
    logger: Logger | None = None,
    log_prefix: str = "pno",
) -> None:
    stage_reviews_dir = diagnostics_dir / "stage_reviews"
    stage_reviews_dir.mkdir(parents=True, exist_ok=True)
    chart_stage_ids = set(PNO_STAGE_SEQUENCE) if render_charts else set()
    manifest_rows: list[dict[str, object]] = []
    selected_stage_set = set(selected_stage_ids)
    passed_chart_stage_id_set = set(selected_stage_ids if passed_chart_stage_ids is None else passed_chart_stage_ids)
    needs_entry_frames = bool(selected_stage_set.intersection(PNO_STAGE_SEQUENCE[1:]))
    prepared_frames_by_symbol: dict[str, tuple[pd.DataFrame, pd.DataFrame, int, int]] = {}
    review_rejections_by_stage: dict[str, dict[str, list[dict[str, object]]]] = {}
    for stage_id in selected_stage_ids:
        filtered_groups: dict[str, list[dict[str, object]]] = {}
        for reason, rows in stage_rejections_by_stage.get(stage_id, {}).items():
            selected_rows = _select_stage_review_rejection_rows(stage_id=stage_id, reason=reason, rows=rows)
            if selected_rows:
                filtered_groups[reason] = selected_rows
        review_rejections_by_stage[stage_id] = filtered_groups
    total_review_charts = 0
    if render_charts:
        for stage_id in selected_stage_ids:
            if stage_id in passed_chart_stage_id_set:
                total_review_charts += len(stage_rows_by_stage.get(stage_id, []))
            rejection_groups = review_rejections_by_stage.get(stage_id, {})
            total_review_charts += sum(len(rows) for rows in rejection_groups.values())
    rendered_review_charts = 0
    review_start_time = time.monotonic()
    last_progress_log_time = review_start_time

    def _log_review_progress(stage_id: str) -> None:
        nonlocal last_progress_log_time
        if logger is None or total_review_charts <= 0:
            return
        if rendered_review_charts <= 0:
            return
        now = time.monotonic()
        if (
            rendered_review_charts == total_review_charts
            or rendered_review_charts % 50 == 0
            or (now - last_progress_log_time) >= 180.0
        ):
            elapsed = max(time.monotonic() - review_start_time, 1e-9)
            rate = rendered_review_charts / elapsed
            remaining = total_review_charts - rendered_review_charts
            eta_seconds = remaining / rate if rate > 0.0 else None
            logger.debug(
                "Графики проверки стадий: %s из %s, стадия %s, ETA %s.",
                log_prefix,
                rendered_review_charts,
                total_review_charts,
                stage_id,
                _format_eta_compact(eta_seconds),
            )
            last_progress_log_time = now

    if render_charts and logger is not None and total_review_charts > 0:
        logger.debug(
            "Графики проверки стадий: всего %s, стадии %s.",
            log_prefix,
            total_review_charts,
            ",".join(selected_stage_ids),
        )

    def _get_prepared_frames(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, int, int] | None:
        cached = prepared_frames_by_symbol.get(symbol)
        if cached is not None:
            return cached
        mtf_frames = symbol_frames.get(symbol)
        if mtf_frames is None:
            return None
        levels_prepared = _prepare_pno_levels_plot_source(mtf_frames.levels_frame)
        levels_timeframe_ms = int(mtf_frames.levels_timeframe.to_milliseconds())
        entry_timeframe_ms = int(mtf_frames.entry_timeframe.to_milliseconds())
        prepared = (
            levels_prepared,
            _prepare_pno_entry_plot_source_with_ema(
                levels_frame=levels_prepared,
                entry_frame=mtf_frames.entry_frame,
            )
            if needs_entry_frames
            else mtf_frames.entry_frame,
            levels_timeframe_ms,
            entry_timeframe_ms,
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
        if stage_id in chart_stage_ids and stage_id in passed_chart_stage_id_set:
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
                    levels_timeframe_ms=prepared_frames[2],
                    entry_timeframe_ms=prepared_frames[3],
                )
                if chart_path is not None:
                    passed_chart_paths.append(chart_path)
                rendered_review_charts += 1
                _log_review_progress(stage_id)

        rejection_groups = stage_rejections_by_stage.get(stage_id, {})
        review_rejection_groups = review_rejections_by_stage.get(stage_id, {})
        rejected_total = 0
        rejected_review_total = 0
        rejected_chart_paths = 0
        rejected_dir = stage_dir / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        for reason, rows in sorted(rejection_groups.items()):
            rejected_total += len(rows)
            review_rows = review_rejection_groups.get(reason, [])
            rejected_review_total += len(review_rows)
            reason_dir = rejected_dir / _sanitize_plot_name(reason)
            reason_dir.mkdir(parents=True, exist_ok=True)
            export_rows = review_rows if review_rows else rows
            pd.DataFrame(export_rows).to_csv(reason_dir / "events.csv", index=False)
            if review_rows and stage_id in chart_stage_ids:
                charts_dir = reason_dir / "charts"
                charts_dir.mkdir(parents=True, exist_ok=True)
                for row_index, row in enumerate(review_rows, start=1):
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
                        levels_timeframe_ms=prepared_frames[2],
                        entry_timeframe_ms=prepared_frames[3],
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
                "rejected_review_count": int(rejected_review_total),
                "rejected_filtered_count": int(rejected_total - rejected_review_total),
                "passed_events_path": str(passed_dir / "events.csv"),
                "passed_charts_count": int(len(passed_chart_paths)),
                "rejected_charts_count": int(rejected_chart_paths),
            }
        )

    pd.DataFrame(manifest_rows).to_csv(stage_reviews_dir / "manifest.csv", index=False)
    if render_charts and logger is not None and total_review_charts > 0:
        logger.debug(
            "Графики проверки стадий готовы: %s.",
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


def _resolve_pno_trade_key(row: dict[str, object]) -> str | None:
    symbol = str(row.get("symbol") or "")
    entry_timestamp_ms = _safe_int(row.get("entry_timestamp_ms"))
    if not symbol or entry_timestamp_ms is None:
        return None
    stage_key = _resolve_pno_stage_key_from_row(row)
    if stage_key:
        return f"{stage_key}|{entry_timestamp_ms}"
    entry_price = _safe_float(row.get("entry_price_actual")) or _safe_float(row.get("entry_price")) or _safe_float(row.get("entry_plan"))
    if entry_price is not None:
        return f"{symbol}|{entry_timestamp_ms}|{entry_price:.8f}"
    return f"{symbol}|{entry_timestamp_ms}"


def _build_pno_trade_exit_reference_row(row: dict[str, object]) -> dict[str, object]:
    entry_price = _safe_float(row.get("entry_price_actual")) or _safe_float(row.get("entry_price")) or _safe_float(row.get("entry_plan"))
    initial_stop_loss = _safe_float(row.get("initial_stop_loss")) or _safe_float(row.get("sl_actual")) or _safe_float(row.get("sl_plan"))
    initial_risk = _safe_float(row.get("initial_risk"))
    if initial_risk is None and entry_price is not None and initial_stop_loss is not None:
        initial_risk = max(entry_price - initial_stop_loss, 0.0)
    tp1 = _safe_float(row.get("tp1"))
    tp2 = _safe_float(row.get("tp2"))
    active_high = _safe_float(row.get("active_high"))
    be_arm_price = _safe_float(row.get("be_arm_price"))
    be_protect_price = _safe_float(row.get("be_protect_price"))
    reference: dict[str, object] = {
        "trade_key": _resolve_pno_trade_key(row),
        "stage_key": _resolve_pno_stage_key_from_row(row),
        "symbol": row.get("symbol"),
        "entry_timestamp_ms": _safe_int(row.get("entry_timestamp_ms")),
        "exit_timestamp_ms": _safe_int(row.get("exit_timestamp_ms")),
        "result_type": row.get("result_type"),
        "category": row.get("category"),
        "entry_price": entry_price,
        "initial_stop_loss": initial_stop_loss,
        "initial_risk": initial_risk,
        "initial_risk_pct": round((initial_risk / entry_price) * 100.0, 4) if initial_risk is not None and entry_price is not None and entry_price > 0.0 else np.nan,
        "fee_rate": _safe_float(row.get("fee_rate")),
        "active_high": active_high,
        "active_high_r": _safe_float(row.get("active_high_r")),
        "tp1": tp1,
        "tp1_r": _safe_float(row.get("tp1_r")),
        "tp2": tp2,
        "tp2_r": _safe_float(row.get("tp2_r")),
        "tp1_share": _safe_float(row.get("tp1_share")),
        "runner_share": _safe_float(row.get("runner_share")),
        "be_arm_price": be_arm_price,
        "be_arm_r": _safe_float(row.get("be_arm_r")),
        "be_protect_price": be_protect_price,
        "be_protect_r": _safe_float(row.get("be_protect_r")),
        "be_arm_timestamp_ms": _safe_int(row.get("be_arm_timestamp_ms")),
        "partial_exit_price": _safe_float(row.get("partial_exit_price")),
        "partial_exit_timestamp_ms": _safe_int(row.get("partial_exit_timestamp_ms")),
        "runner_exit_price": _safe_float(row.get("runner_exit_price")),
        "runner_exit_timestamp_ms": _safe_int(row.get("runner_exit_timestamp_ms")),
        "runner_exit_reason": row.get("runner_exit_reason"),
        "be_armed_pre_tp1": row.get("be_armed_pre_tp1"),
        "pnl": _safe_float(row.get("pnl")),
        "pnl_percent": _safe_float(row.get("pnl_percent")),
        "mfe_r": _safe_float(row.get("mfe_r")),
        "mae_r": _safe_float(row.get("mae_r")),
        "level": _safe_float(row.get("level")),
        "entry_plan": _safe_float(row.get("entry_plan")),
        "entry_confirmation_mode": row.get("entry_confirmation_mode"),
        "pullback_base_low": _safe_float(row.get("pullback_base_low")),
        "pullback_base_high": _safe_float(row.get("pullback_base_high")),
        "pullback_base_quality": _safe_float(row.get("pullback_base_quality")),
        "overhead_resistance_score": _safe_float(row.get("overhead_resistance_score")),
        "overhead_red_body_share": _safe_float(row.get("overhead_red_body_share")),
        "overhead_red_count": _safe_int(row.get("overhead_red_count")),
        "dominant_overhead_red_high": _safe_float(row.get("dominant_overhead_red_high")),
        "dominant_overhead_red_body": _safe_float(row.get("dominant_overhead_red_body")),
        "be_arm_to_active_high_fraction": _safe_float(row.get("be_arm_to_active_high_fraction")),
        "be_buffer_r_fraction": _safe_float(row.get("be_buffer_r_fraction")),
    }
    return reference


def _build_pno_trade_path_rows(
    *,
    entry_frame: pd.DataFrame,
    row: dict[str, object],
    max_bars: int = _PNO_RESEARCH_TRADE_PATH_MAX_BARS,
) -> list[dict[str, object]]:
    entry_timestamp_ms = _safe_int(row.get("entry_timestamp_ms"))
    if entry_frame.empty or entry_timestamp_ms is None:
        return []
    trade_key = _resolve_pno_trade_key(row)
    entry_price = _safe_float(row.get("entry_price_actual")) or _safe_float(row.get("entry_price")) or _safe_float(row.get("entry_plan"))
    initial_stop_loss = _safe_float(row.get("initial_stop_loss")) or _safe_float(row.get("sl_actual")) or _safe_float(row.get("sl_plan"))
    initial_risk = _safe_float(row.get("initial_risk"))
    if initial_risk is None and entry_price is not None and initial_stop_loss is not None:
        initial_risk = max(entry_price - initial_stop_loss, 0.0)
    entry_idx = int(np.searchsorted(entry_frame["timestamp"].to_numpy(dtype=np.int64, copy=False), int(entry_timestamp_ms), side="left"))
    if entry_idx >= len(entry_frame):
        return []
    path_frame = entry_frame.iloc[entry_idx : min(entry_idx + max_bars + 1, len(entry_frame))].copy()
    if path_frame.empty:
        return []
    active_high = _safe_float(row.get("active_high"))
    level = _safe_float(row.get("level"))
    be_arm_price = _safe_float(row.get("be_arm_price"))
    be_protect_price = _safe_float(row.get("be_protect_price"))
    tp1 = _safe_float(row.get("tp1"))
    tp2 = _safe_float(row.get("tp2"))
    trade_rows: list[dict[str, object]] = []
    running_high = -np.inf
    running_low = np.inf
    for offset, (_, path_row) in enumerate(path_frame.iterrows()):
        open_price = float(path_row["open"])
        high_price = float(path_row["high"])
        low_price = float(path_row["low"])
        close_price = float(path_row["close"])
        volume = float(path_row["volume"])
        running_high = max(running_high, high_price)
        running_low = min(running_low, low_price)
        trade_rows.append(
            {
                "trade_key": trade_key,
                "symbol": row.get("symbol"),
                "result_type": row.get("result_type"),
                "bar_offset": int(offset),
                "timestamp_ms": int(path_row["timestamp"]),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
                "open_r": round((open_price - entry_price) / initial_risk, 6) if entry_price is not None and initial_risk and initial_risk > 0.0 else np.nan,
                "high_r": round((high_price - entry_price) / initial_risk, 6) if entry_price is not None and initial_risk and initial_risk > 0.0 else np.nan,
                "low_r": round((low_price - entry_price) / initial_risk, 6) if entry_price is not None and initial_risk and initial_risk > 0.0 else np.nan,
                "close_r": round((close_price - entry_price) / initial_risk, 6) if entry_price is not None and initial_risk and initial_risk > 0.0 else np.nan,
                "cum_mfe_r": round((running_high - entry_price) / initial_risk, 6) if entry_price is not None and initial_risk and initial_risk > 0.0 else np.nan,
                "cum_mae_r": round((entry_price - running_low) / initial_risk, 6) if entry_price is not None and initial_risk and initial_risk > 0.0 else np.nan,
                "crossed_initial_stop": bool(initial_stop_loss is not None and low_price <= initial_stop_loss),
                "crossed_be_arm": bool(be_arm_price is not None and high_price >= be_arm_price),
                "crossed_be_protect": bool(be_protect_price is not None and low_price <= be_protect_price),
                "crossed_active_high": bool(active_high is not None and high_price >= active_high),
                "crossed_tp1": bool(tp1 is not None and high_price >= tp1),
                "crossed_tp2": bool(tp2 is not None and high_price >= tp2),
                "crossed_level_down": bool(level is not None and low_price <= level),
                "signal_close_above_level": bool(level is not None and close_price > level),
            }
        )
    return trade_rows


def _build_pno_levels_path_rows(
    *,
    levels_frame: pd.DataFrame,
    row: dict[str, object],
    source_status: str,
    source_reason: str,
) -> list[dict[str, object]]:
    pump_start_timestamp_ms = _safe_int(row.get("pump_start_timestamp_ms"))
    signal_timestamp_ms = (
        _safe_int(row.get("entry_signal_timestamp_ms"))
        or _safe_int(row.get("timestamp_ms"))
        or _safe_int(row.get("entry_timestamp_ms"))
    )
    if levels_frame.empty or pump_start_timestamp_ms is None or signal_timestamp_ms is None:
        return []
    path_frame = _slice_pno_frame_by_timestamp(
        levels_frame,
        start_timestamp_ms=pump_start_timestamp_ms,
        end_timestamp_ms=signal_timestamp_ms,
    )
    if path_frame.empty:
        return []
    active_high_timestamp_ms = _safe_int(row.get("active_high_timestamp_ms"))
    pullback_low_timestamp_ms = _safe_int(row.get("pullback_low_timestamp_ms"))
    level_first_timestamp_ms = _safe_int(row.get("level_first_local_high_timestamp_ms"))
    level_last_timestamp_ms = _safe_int(row.get("level_last_local_high_timestamp_ms"))
    trade_key = _resolve_pno_trade_key(row)
    stage_key = _resolve_pno_stage_key_from_row(row)
    path_timestamps = path_frame["timestamp"].to_numpy(dtype=np.int64, copy=False)

    def _resolve_levels_idx(timestamp_ms: int | None) -> int | None:
        if timestamp_ms is None or path_timestamps.size == 0:
            return None
        idx = int(np.searchsorted(path_timestamps, int(timestamp_ms), side="right") - 1)
        if idx < 0:
            return None
        return min(idx, path_timestamps.size - 1)

    active_high_levels_idx = _resolve_levels_idx(active_high_timestamp_ms)
    pullback_low_levels_idx = _resolve_levels_idx(pullback_low_timestamp_ms)
    level_first_levels_idx = _resolve_levels_idx(level_first_timestamp_ms)
    level_last_levels_idx = _resolve_levels_idx(level_last_timestamp_ms)
    signal_levels_idx = _resolve_levels_idx(signal_timestamp_ms)
    path_rows: list[dict[str, object]] = []
    prev_body_low: float | None = None
    prev_body_high: float | None = None
    for offset, (_, path_row) in enumerate(path_frame.iterrows()):
        open_price = float(path_row["open"])
        high_price = float(path_row["high"])
        low_price = float(path_row["low"])
        close_price = float(path_row["close"])
        bar_range = max(high_price - low_price, 1e-12)
        body_low = min(open_price, close_price)
        body_high = max(open_price, close_price)
        overlap_prev = (
            prev_body_low is not None
            and prev_body_high is not None
            and body_low <= prev_body_high
            and body_high >= prev_body_low
        )
        timestamp_ms = int(path_row["timestamp"])
        path_rows.append(
            {
                "trade_key": trade_key,
                "stage_key": stage_key,
                "symbol": row.get("symbol"),
                "source_stage": PNO_STAGE_5_TRADE,
                "source_status": source_status,
                "source_reason": source_reason,
                "bar_offset": int(offset),
                "timestamp_ms": timestamp_ms,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": float(path_row["volume"]),
                "ema9": float(path_row["ema9"]) if "ema9" in path_row.index else np.nan,
                "ema20": float(path_row["ema20"]) if "ema20" in path_row.index else np.nan,
                "body_share": round(abs(close_price - open_price) / bar_range, 4),
                "upper_wick_share": round((high_price - max(open_price, close_price)) / bar_range, 4),
                "lower_wick_share": round((min(open_price, close_price) - low_price) / bar_range, 4),
                "body_overlap_prev": bool(overlap_prev),
                "is_green": bool(close_price >= open_price),
                "is_pump_start_bar": bool(timestamp_ms == pump_start_timestamp_ms),
                "is_active_high_bar": bool(active_high_levels_idx is not None and offset == active_high_levels_idx),
                "is_pullback_low_bar": bool(pullback_low_levels_idx is not None and offset == pullback_low_levels_idx),
                "is_level_first_bar": bool(level_first_levels_idx is not None and offset == level_first_levels_idx),
                "is_level_last_bar": bool(level_last_levels_idx is not None and offset == level_last_levels_idx),
                "is_signal_bar": bool(signal_levels_idx is not None and offset == signal_levels_idx),
            }
        )
        prev_body_low = body_low
        prev_body_high = body_high
    return path_rows


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


def _slice_backtest_frame_window(
    frame: pd.DataFrame,
    *,
    days: int | None,
    end_timestamp_ms: int | None,
) -> pd.DataFrame:
    if frame.empty or days is None:
        return frame
    if "timestamp" not in frame.columns:
        return frame
    timestamps = frame["timestamp"].to_numpy(dtype=np.int64, copy=False)
    if timestamps.size == 0:
        return frame
    resolved_end = int(end_timestamp_ms) if end_timestamp_ms is not None else int(timestamps[-1])
    start_timestamp_ms = resolved_end - (int(days) * 86_400_000)
    return _slice_pno_frame_by_timestamp(
        frame,
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=resolved_end,
    )


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
    active_high_levels_timestamp_ms: int | None = None

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
            pump_timestamps = pump_window["timestamp"].to_numpy(dtype=np.int64, copy=False)
            green_bodies = np.maximum(closes - opens, 0.0)
            red_bodies = np.maximum(opens - closes, 0.0)
            ranges = np.maximum(highs - lows, 1e-12)
            result["pump_first_bar_share_of_leg"] = round(float(green_bodies[0] / leg_size), 4)
            result["pump_best_bar_share_of_leg"] = round(float(np.max(green_bodies) / leg_size), 4)
            result["pump_last_bar_share_of_leg"] = round(float(green_bodies[-1] / leg_size), 4)
            result["pump_shape_max_red_body_share_leg"] = round(float(np.max(red_bodies) / leg_size), 4)
            result["pump_shape_counterflow_ratio"] = round(float(np.sum(red_bodies) / max(np.sum(green_bodies), 1e-12)), 4)
            result["pump_shape_micro_flat_share"] = round(float(np.mean((np.abs(closes - opens) / ranges) <= 0.12)), 4)
            front_idx = max(1, int(np.ceil(len(pump_window) / 3.0)))
            front_move = float(np.max(highs[:front_idx]) - np.min(lows[:front_idx])) if front_idx > 0 else 0.0
            result["pump_front_third_share_of_leg"] = round(front_move / leg_size, 4)
            running_peak = np.maximum.accumulate(highs)
            result["pump_internal_drawdown_share"] = round(float(np.max((running_peak - lows) / leg_size)), 4)
            active_high_idx = max(0, int(np.searchsorted(pump_timestamps, int(active_high_timestamp_ms), side="right") - 1))
            active_high_levels_timestamp_ms = int(pump_timestamps[min(active_high_idx, len(pump_timestamps) - 1)])
            result["active_high_levels_timestamp_ms"] = active_high_levels_timestamp_ms
            high_bar_open = float(opens[active_high_idx])
            high_bar_high = float(highs[active_high_idx])
            high_bar_low = float(lows[active_high_idx])
            high_bar_close = float(closes[active_high_idx])
            high_bar_range = max(high_bar_high - high_bar_low, 1e-12)
            high_bar_volume = float(pump_window["volume"].iloc[active_high_idx])
            result["active_high_bar_body_share"] = round(abs(high_bar_close - high_bar_open) / high_bar_range, 4)
            result["active_high_bar_upper_wick_share"] = round((high_bar_high - max(high_bar_open, high_bar_close)) / high_bar_range, 4)
            result["active_high_bar_lower_wick_share"] = round((min(high_bar_open, high_bar_close) - high_bar_low) / high_bar_range, 4)
            result["active_high_bar_close_position"] = round((high_bar_close - high_bar_low) / high_bar_range, 4)
            result["active_high_bar_is_green"] = bool(high_bar_close >= high_bar_open)
            result["active_high_bar_volume_vs_pump_median"] = round(
                high_bar_volume / float(np.nanmedian(pump_window["volume"].to_numpy(dtype=np.float64, copy=False))),
                4,
            ) if float(np.nanmedian(pump_window["volume"].to_numpy(dtype=np.float64, copy=False))) > 0.0 else np.nan

    if active_high_timestamp_ms is not None and signal_ts is not None and signal_ts > active_high_timestamp_ms:
        post_high_levels_window = _slice_pno_frame_by_timestamp(
            levels_frame,
            start_timestamp_ms=(active_high_levels_timestamp_ms + levels_step_ms) if active_high_levels_timestamp_ms is not None else (active_high_timestamp_ms + levels_step_ms),
            end_timestamp_ms=signal_ts,
        )
        post_high_levels_profile = _resolve_pno_window_profile(
            post_high_levels_window,
            prefix="post_high_levels",
            anchor_price=active_high or 1.0,
        )
        result.update(post_high_levels_profile)
        if not post_high_levels_window.empty:
            opens = post_high_levels_window["open"].to_numpy(dtype=np.float64, copy=False)
            highs = post_high_levels_window["high"].to_numpy(dtype=np.float64, copy=False)
            lows = post_high_levels_window["low"].to_numpy(dtype=np.float64, copy=False)
            closes = post_high_levels_window["close"].to_numpy(dtype=np.float64, copy=False)
            red_bodies = np.maximum(opens - closes, 0.0)
            ranges = np.maximum(highs - lows, 1e-12)
            upper_wicks = np.maximum(highs - np.maximum(opens, closes), 0.0)
            lower_wicks = np.maximum(np.minimum(opens, closes) - lows, 0.0)
            if "ema20" in post_high_levels_window.columns:
                ema20 = post_high_levels_window["ema20"].to_numpy(dtype=np.float64, copy=False)
                result["post_high_levels_ema20_pierce_count"] = int(np.sum(lows <= ema20))
                result["post_high_levels_close_below_ema20_share"] = round(float(np.mean(closes <= ema20)), 4)
            body_lows = np.minimum(opens, closes)
            body_highs = np.maximum(opens, closes)
            overlap_rate = np.nan
            if body_lows.size >= 2:
                overlaps = (body_lows[1:] <= body_highs[:-1]) & (body_highs[1:] >= body_lows[:-1])
                overlap_rate = float(np.mean(overlaps))
            result["post_high_levels_body_overlap_rate"] = round(overlap_rate, 4) if np.isfinite(overlap_rate) else np.nan
            result["post_high_levels_wick_share"] = round(float(np.sum(upper_wicks + lower_wicks) / max(np.sum(ranges), 1e-12)), 4)
            result["post_high_levels_max_red_body_share_leg"] = round(float(np.max(red_bodies) / max(leg_size or np.nan, 1e-12)), 4) if leg_size is not None and leg_size > 0.0 else np.nan

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


def _resolve_pno_setup_family(payload: dict[str, object]) -> tuple[str, str]:
    signal_ts = _safe_int(payload.get("entry_signal_timestamp_ms")) or _safe_int(payload.get("signal_bar_timestamp_ms"))
    active_high_ts = _safe_int(payload.get("active_high_timestamp_ms"))
    pump_start_ts = _safe_int(payload.get("pump_start_timestamp_ms"))
    level_valid_ts = _safe_int(payload.get("level_valid_timestamp_ms"))
    active_high_to_signal_minutes = (
        max((signal_ts - active_high_ts) / 60_000.0, 0.0)
        if signal_ts is not None and active_high_ts is not None
        else _safe_float(payload.get("active_high_to_signal_minutes"))
    )
    pump_to_signal_minutes = (
        max((signal_ts - pump_start_ts) / 60_000.0, 0.0)
        if signal_ts is not None and pump_start_ts is not None
        else _safe_float(payload.get("pump_to_signal_minutes"))
    )
    level_life_minutes = (
        max((signal_ts - level_valid_ts) / 60_000.0, 0.0)
        if signal_ts is not None and level_valid_ts is not None
        else _safe_float(payload.get("level_life_minutes"))
    )
    signal_ema_spread_pct = _safe_float(payload.get("signal_bar_ema_spread_pct"))
    signal_ema20_slope_3 = _safe_float(payload.get("signal_bar_ema20_slope_3"))
    overhead_resistance_score = _safe_float(payload.get("overhead_resistance_score"))
    pump_counterflow_ratio_5m = _safe_float(payload.get("pump_counterflow_ratio_5m"))
    entry_pos = _safe_float(payload.get("entry_pos"))

    if (
        active_high_to_signal_minutes is not None
        and level_life_minutes is not None
        and signal_ema_spread_pct is not None
        and signal_ema20_slope_3 is not None
        and entry_pos is not None
        and pump_counterflow_ratio_5m is not None
        and active_high_to_signal_minutes <= 35.0
        and level_life_minutes <= 20.0
        and entry_pos <= 0.58
        and pump_counterflow_ratio_5m <= 0.03
        and (
            signal_ema_spread_pct >= 1.8
            or (signal_ema_spread_pct >= 1.2 and signal_ema20_slope_3 >= 0.22)
        )
    ):
        return "impulse_followthrough", "core"

    if (
        active_high_to_signal_minutes is not None
        and level_life_minutes is not None
        and signal_ema_spread_pct is not None
        and signal_ema20_slope_3 is not None
        and overhead_resistance_score is not None
        and entry_pos is not None
        and pump_counterflow_ratio_5m is not None
        and active_high_to_signal_minutes <= 35.0
        and level_life_minutes <= 20.0
        and entry_pos <= 0.60
        and overhead_resistance_score <= 0.62
        and pump_counterflow_ratio_5m <= 0.03
        and signal_ema_spread_pct >= 0.45
        and signal_ema20_slope_3 >= 0.08
    ):
        return "fresh_reclaim", "research"

    if (
        active_high_to_signal_minutes is not None
        and level_life_minutes is not None
        and signal_ema_spread_pct is not None
        and signal_ema20_slope_3 is not None
        and overhead_resistance_score is not None
        and entry_pos is not None
        and pump_counterflow_ratio_5m is not None
        and 35.0 < active_high_to_signal_minutes <= 120.0
        and level_life_minutes <= 120.0
        and entry_pos <= 0.66
        and overhead_resistance_score <= 0.58
        and pump_counterflow_ratio_5m <= 0.03
        and signal_ema_spread_pct >= 0.35
        and signal_ema20_slope_3 >= 0.04
    ):
        return "late_shelf_reclaim", "research"

    if (
        active_high_to_signal_minutes is not None
        and (
            active_high_to_signal_minutes > 120.0
            or (level_life_minutes is not None and level_life_minutes > 35.0)
            or (pump_to_signal_minutes is not None and pump_to_signal_minutes > 180.0)
        )
    ):
        return "stale_reclaim", "mixed"

    return "mixed_other", "mixed"


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
    pump_body_share_mean = _safe_float(payload.get("pump_body_share_mean")) or _safe_float(payload.get("pump_shape_body_share_avg"))
    pump_wick_share = _safe_float(payload.get("pump_wick_share"))
    pump_micro_flat_share = _safe_float(payload.get("pump_micro_flat_bar_share"))
    if pump_micro_flat_share is None:
        pump_micro_flat_share = _safe_float(payload.get("pump_shape_micro_flat_share"))
    pump_max_red_5m_share = _safe_float(payload.get("pump_max_red_body_share_5m"))
    if pump_max_red_5m_share is None:
        pump_max_red_5m_share = _safe_float(payload.get("pump_shape_max_red_body_share_leg"))
    pump_counterflow_5m = _safe_float(payload.get("pump_counterflow_ratio_5m"))
    if pump_counterflow_5m is None:
        pump_counterflow_5m = _safe_float(payload.get("pump_shape_counterflow_ratio"))
    post_high_wick_share = _safe_float(payload.get("post_high_wick_share"))
    if post_high_wick_share is None:
        post_high_wick_share = _safe_float(payload.get("post_high_levels_wick_share"))
    post_high_overlap_rate = _safe_float(payload.get("post_high_body_overlap_rate"))
    if post_high_overlap_rate is None:
        post_high_overlap_rate = _safe_float(payload.get("post_high_levels_body_overlap_rate"))
    pullback_base_low = _safe_float(payload.get("pullback_base_low"))
    pullback_base_high = _safe_float(payload.get("pullback_base_high"))
    overhead_resistance_score = _safe_float(payload.get("overhead_resistance_score"))
    overhead_red_body_share = _safe_float(payload.get("overhead_red_body_share"))
    dominant_overhead_red_body = _safe_float(payload.get("dominant_overhead_red_body"))
    span = (active_high - pullback_low) if active_high is not None and pullback_low is not None else None
    level_pos = entry_pos
    if level_pos is None and level is not None and span is not None and span > 0.0:
        level_pos = (level - pullback_low) / span

    payload["stage_key"] = _resolve_pno_stage_key_from_row(payload)
    payload["trade_key"] = _resolve_pno_trade_key(payload)
    payload["pump_leg_pct"] = round((leg_size / leg_start) * 100.0, 4) if leg_size is not None and leg_start is not None and leg_start > 0.0 else np.nan
    payload["pullback_fraction_of_leg"] = round(pullback_depth / leg_size, 4) if pullback_depth is not None and leg_size is not None and leg_size > 0.0 else np.nan
    payload["level_fraction_of_pullback"] = round(level_pos, 4) if level_pos is not None and np.isfinite(level_pos) else np.nan
    payload["pump_body_wick_edge"] = round(float(pump_body_share_mean - pump_wick_share), 4) if pump_body_share_mean is not None and pump_wick_share is not None else np.nan
    payload["pullback_base_height"] = round(float(pullback_base_high - pullback_base_low), 8) if pullback_base_low is not None and pullback_base_high is not None else np.nan
    payload["pullback_base_contains_low"] = bool(
        pullback_base_low is not None
        and pullback_base_high is not None
        and pullback_low is not None
        and pullback_base_low <= pullback_low <= pullback_base_high
    )
    payload["dominant_overhead_red_body_r"] = round(float(dominant_overhead_red_body / leg_size), 6) if dominant_overhead_red_body is not None and leg_size is not None and leg_size > 0.0 else np.nan
    payload["overhead_resistance_score_norm"] = round(float(overhead_resistance_score), 4) if overhead_resistance_score is not None else np.nan
    payload["overhead_red_body_share_norm"] = round(float(overhead_red_body_share), 4) if overhead_red_body_share is not None else np.nan
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
    payload["pump_body_share_bucket"] = _bucketize_pno_value(pump_body_share_mean, thresholds=(0.35, 0.50, 0.65), labels=("body_small", "body_ok", "body_strong", "body_expansion"))
    payload["pump_flat_body_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pump_flat_body_share")), thresholds=(0.10, 0.20, 0.35), labels=("flat_low", "flat_ok", "flat_high", "flat_excessive"))
    payload["pump_body_wick_edge_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pump_body_wick_edge")), thresholds=(-0.05, 0.05, 0.20), labels=("body_lt_wick", "body_eq_wick", "body_gt_wick", "body_dominant"))
    payload["pump_micro_flat_bucket"] = _bucketize_pno_value(pump_micro_flat_share, thresholds=(0.05, 0.15, 0.30), labels=("microflat_low", "microflat_ok", "microflat_high", "microflat_excessive"))
    payload["active_high_upper_wick_bucket"] = _bucketize_pno_value(_safe_float(payload.get("active_high_bar_upper_wick_share")), thresholds=(0.15, 0.35, 0.55), labels=("high_wick_tight", "high_wick_ok", "high_wick_heavy", "high_wick_extreme"))
    payload["active_high_close_position_bucket"] = _bucketize_pno_value(_safe_float(payload.get("active_high_bar_close_position")), thresholds=(0.35, 0.60, 0.80), labels=("high_close_low", "high_close_mid", "high_close_high", "high_close_top"))
    payload["pump_max_red_5m_bucket"] = _bucketize_pno_value(pump_max_red_5m_share, thresholds=(0.10, 0.25, 0.40), labels=("red5m_small", "red5m_ok", "red5m_heavy", "red5m_extreme"))
    payload["pump_counterflow_5m_bucket"] = _bucketize_pno_value(pump_counterflow_5m, thresholds=(0.15, 0.35, 0.60), labels=("counter5m_low", "counter5m_ok", "counter5m_heavy", "counter5m_extreme"))
    payload["pump_max_red_1m_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pump_max_red_body_share_1m")), thresholds=(0.15, 0.30, 0.50), labels=("red1m_small", "red1m_ok", "red1m_heavy", "red1m_extreme"))
    payload["pump_counterflow_1m_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pump_counterflow_ratio_1m")), thresholds=(0.20, 0.45, 0.75), labels=("counter1m_low", "counter1m_ok", "counter1m_heavy", "counter1m_extreme"))
    payload["pre_pump_ema_cross_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pre_pump_ema_crosses_1h")), thresholds=(1.0, 2.0), labels=("ema_cross_1", "ema_cross_2", "ema_cross_3plus"))
    payload["pullback_depth_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pullback_fraction_of_leg")), thresholds=(0.25, 0.40, 0.55), labels=("pb_shallow", "pb_balanced", "pb_deep", "pb_very_deep"))
    payload["pullback_age_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pullback_age_bars")), thresholds=(3.0, 6.0, 10.0), labels=("pb_fast", "pb_normal", "pb_slow", "pb_stale"))
    payload["post_high_wick_bucket"] = _bucketize_pno_value(post_high_wick_share, thresholds=(0.35, 0.55, 0.75), labels=("post_wick_light", "post_wick_ok", "post_wick_heavy", "post_wick_extreme"))
    payload["post_high_overlap_bucket"] = _bucketize_pno_value(post_high_overlap_rate, thresholds=(0.20, 0.45, 0.70), labels=("post_overlap_low", "post_overlap_ok", "post_overlap_high", "post_overlap_extreme"))
    post_high_pierces = _safe_int(payload.get("post_high_ema20_pierce_count"))
    payload["post_high_ema20_pierce_bucket"] = "na" if post_high_pierces is None else ("pierce_0" if post_high_pierces <= 0 else "pierce_1" if post_high_pierces == 1 else "pierce_2plus")
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
    payload["pullback_base_quality_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pullback_base_quality")), thresholds=(0.3, 0.8, 1.2), labels=("base_weak", "base_ok", "base_strong", "base_elite"))
    payload["pullback_base_left_vacuum_bucket"] = _bucketize_pno_value(_safe_float(payload.get("pullback_base_left_vacuum")), thresholds=(0.25, 0.50, 0.75), labels=("vacuum_low", "vacuum_ok", "vacuum_strong", "vacuum_clear"))
    payload["overhead_resistance_bucket"] = _bucketize_pno_value(overhead_resistance_score, thresholds=(0.20, 0.45, 0.80), labels=("overhead_light", "overhead_working", "overhead_heavy", "overhead_extreme"))
    payload["overhead_red_body_bucket"] = _bucketize_pno_value(overhead_red_body_share, thresholds=(0.10, 0.25, 0.45), labels=("red_light", "red_working", "red_heavy", "red_extreme"))
    payload["overhead_red_count_bucket"] = _bucketize_pno_value(_safe_float(payload.get("overhead_red_count")), thresholds=(0.0, 1.0, 3.0), labels=("red_0", "red_1", "red_2_3", "red_4plus"))
    payload["signal_ema_spread_bucket"] = _bucketize_pno_value(_safe_float(payload.get("signal_bar_ema_spread_pct")), thresholds=(0.15, 0.40, 0.80), labels=("ema_tight", "ema_ok", "ema_open", "ema_extended"))
    payload["signal_followthrough_bucket"] = _bucketize_pno_value(_safe_float(payload.get("signal_followthrough_3bar_pct")), thresholds=(0.1, 0.4, 1.0), labels=("ft_flat", "ft_ok", "ft_strong", "ft_explosive"))
    payload["prior_impulse_bucket"] = _bucketize_pno_value(_safe_float(payload.get("prior_6h_impulse_count")), thresholds=(0.0, 1.0, 2.0), labels=("prior_clean", "prior_one", "prior_two", "prior_many"))
    payload["overhead_supply_bucket"] = _bucketize_pno_value(_safe_float(payload.get("left_supply_bars_above_level_24h")), thresholds=(0.0, 3.0, 10.0), labels=("supply_clear", "supply_light", "supply_medium", "supply_heavy"))
    payload["hold_status_group"] = str(payload.get("hold_status_at_validation") or payload.get("hold_status_at_level_search") or "na")
    payload["leg_start_status_group"] = str(payload.get("leg_start_status_at_validation") or "na")
    setup_family, setup_family_tier = _resolve_pno_setup_family(payload)
    payload["setup_family"] = setup_family
    payload["setup_family_tier"] = setup_family_tier
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
        "pump_body_share_bucket",
        "pump_flat_body_bucket",
        "pump_body_wick_edge_bucket",
        "pump_micro_flat_bucket",
        "active_high_upper_wick_bucket",
        "active_high_close_position_bucket",
        "pump_max_red_5m_bucket",
        "pump_counterflow_5m_bucket",
        "pump_max_red_1m_bucket",
        "pump_counterflow_1m_bucket",
        "sleep_compression_bucket",
        "pump_shape_bucket",
        "pump_drawdown_bucket",
        "pre_pump_ema_cross_bucket",
        "pullback_depth_bucket",
        "pullback_age_bucket",
        "post_high_wick_bucket",
        "post_high_overlap_bucket",
        "post_high_ema20_pierce_bucket",
        "pullback_ema_hold_bucket",
        "level_maturity_bucket",
        "touches_bucket",
        "entry_zone_bucket",
        "level_life_bucket",
        "level_false_break_bucket",
        "pullback_base_quality_bucket",
        "pullback_base_left_vacuum_bucket",
        "overhead_resistance_bucket",
        "overhead_red_body_bucket",
        "overhead_red_count_bucket",
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
        "setup_family",
        "setup_family_tier",
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
    logger: Logger | None = None,
) -> None:
    research_dir = diagnostics_dir / "research_context"
    research_dir.mkdir(parents=True, exist_ok=True)
    prepared_levels_frames: dict[str, pd.DataFrame] = {}
    prepared_entry_frames: dict[str, pd.DataFrame] = {}
    export_start_time = time.monotonic()
    last_progress_log_time = export_start_time

    def _log_progress(phase: str, done: int, total: int, *, force: bool = False) -> None:
        nonlocal last_progress_log_time
        if logger is None or total <= 0:
            return
        now = time.monotonic()
        if not force and (now - last_progress_log_time) < 30.0:
            return
        elapsed = max(now - export_start_time, 1e-9)
        rate = done / elapsed
        remaining = total - done
        eta_seconds = remaining / rate if rate > 0.0 else None
        logger.debug(
            "Экспорт исследовательского контекста: этап %s, %s из %s, ETA %s.",
            phase,
            done,
            total,
            (done / total) * 100.0,
            _format_eta_compact(eta_seconds),
        )
        last_progress_log_time = now

    if logger is not None:
        stage_passed_total = sum(len(rows) for rows in stage_rows_by_stage.values())
        stage_rejected_total = sum(
            len(rows)
            for reason_groups in stage_rejections_by_stage.values()
            for rows in reason_groups.values()
        )

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
    stage_context_total = sum(len(rows) for rows in stage_rows_by_stage.values()) + sum(
        len(rows)
        for reason_groups in stage_rejections_by_stage.values()
        for rows in reason_groups.values()
    )
    stage_context_done = 0
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
            stage_context_done += 1
            _log_progress("stage_context", stage_context_done, stage_context_total)
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
                stage_context_done += 1
                _log_progress("stage_context", stage_context_done, stage_context_total)
    stage_context_frame = pd.DataFrame(stage_context_rows)
    stage_context_frame.to_csv(research_dir / "stage_context_all.csv", index=False)
    _log_progress("stage_context", stage_context_done, stage_context_total, force=True)

    trade_context_rows: list[dict[str, object]] = []
    trade_exit_reference_rows: list[dict[str, object]] = []
    trade_path_rows: list[dict[str, object]] = []
    stage5_levels_path_rows: list[dict[str, object]] = []
    stage5_outcome_by_key: dict[str, str] = {}
    trades_total = len(trade_rows)
    trades_done = 0
    for row in trade_rows:
        symbol = str(row.get("symbol") or "")
        prepared_levels_frame = _get_prepared_levels_frame(symbol)
        prepared_entry_frame = _get_prepared_entry_frame(symbol)
        signal_timestamp_ms = _safe_int(row.get("entry_signal_timestamp_ms")) or _safe_int(row.get("entry_timestamp_ms"))
        signal_context = _resolve_pno_signal_bar_context(
            entry_frame=prepared_entry_frame,
            timestamp_ms=signal_timestamp_ms,
            level=_safe_float(row.get("level")),
        )
        pattern_context = _resolve_pno_pattern_context(
            levels_frame=_get_prepared_levels_frame(symbol),
            entry_frame=prepared_entry_frame,
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
        enriched["is_win"] = result_type in {"be", "tp1_be", "tp2"}
        trade_context_rows.append(enriched)
        trade_exit_reference_rows.append(_build_pno_trade_exit_reference_row(row))
        trade_path_rows.extend(_build_pno_trade_path_rows(entry_frame=prepared_entry_frame, row=row))
        stage5_levels_path_rows.extend(
            _build_pno_levels_path_rows(
                levels_frame=prepared_levels_frame,
                row=row,
                source_status="passed",
                source_reason=str(row.get("result_type") or row.get("category") or "trade"),
            )
        )
        stage_key = str(enriched.get("stage_key") or "")
        if stage_key:
            stage5_outcome_by_key[stage_key] = result_type
        trades_done += 1
        _log_progress("trade_context", trades_done, trades_total)

    trade_context_frame = pd.DataFrame(trade_context_rows)
    trade_context_frame.to_csv(research_dir / "trade_context.csv", index=False)
    pd.DataFrame(trade_exit_reference_rows).to_csv(research_dir / "trade_exit_reference.csv", index=False)
    pd.DataFrame(trade_path_rows).to_csv(research_dir / "trade_path_context.csv", index=False)
    _log_progress("trade_context", trades_done, trades_total, force=True)

    stage5_candidate_rows: list[dict[str, object]] = list(trade_context_rows)
    for reason, rows in stage_rejections_by_stage.get(PNO_STAGE_5_TRADE, {}).items():
        for row in rows:
            symbol = str(row.get("symbol") or "")
            prepared_levels_frame = _get_prepared_levels_frame(symbol)
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
            stage5_levels_path_rows.extend(
                _build_pno_levels_path_rows(
                    levels_frame=prepared_levels_frame,
                    row=row,
                    source_status="rejected",
                    source_reason=reason,
                )
            )
            stage_key = str(enriched.get("stage_key") or "")
            if stage_key:
                stage5_outcome_by_key.setdefault(stage_key, f"rejected_{reason}")

    stage5_candidate_frame = pd.DataFrame(stage5_candidate_rows)
    stage5_candidate_frame.to_csv(research_dir / "stage5_trigger_context.csv", index=False)
    pd.DataFrame(stage5_levels_path_rows).to_csv(research_dir / "stage5_levels_path_context.csv", index=False)

    stage4_context_rows: list[dict[str, object]] = []
    stage4_total = len(stage_rows_by_stage.get(PNO_STAGE_4_LEVEL, [])) + sum(
        len(rows) for rows in stage_rejections_by_stage.get(PNO_STAGE_4_LEVEL, {}).values()
    )
    stage4_done = 0
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
        enriched["downstream_triggered"] = downstream_outcome in {"sl", "be", "tp1_be", "tp2"}
        enriched["downstream_win"] = downstream_outcome in {"be", "tp1_be", "tp2"}
        stage4_context_rows.append(enriched)
        stage4_done += 1
        _log_progress("stage4_context", stage4_done, stage4_total)
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
            stage4_done += 1
            _log_progress("stage4_context", stage4_done, stage4_total)
    stage4_context_frame = pd.DataFrame(stage4_context_rows)
    stage4_context_frame.to_csv(research_dir / "stage4_level_context.csv", index=False)
    _log_progress("stage4_context", stage4_done, stage4_total, force=True)

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

    if logger is not None:
        elapsed = max(time.monotonic() - export_start_time, 0.0)
        logger.debug(
            "Исследовательский контекст сохранён: %s, время %s.",
            research_dir,
            _format_eta_compact(elapsed),
        )


