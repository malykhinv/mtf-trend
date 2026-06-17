"""Neutral matplotlib charting helpers shared by research tools."""

from __future__ import annotations

import re

import matplotlib

matplotlib.use("Agg")
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.patches import Rectangle
from matplotlib.ticker import LinearLocator
from matplotlib.transforms import blended_transform_factory

CHART_FIGURE_FACE = "#08111f"
CHART_AXIS_FACE = "#0f172a"
CHART_GRID = "#334155"
CHART_TEXT = "#dbe4f0"
CHART_MUTED = "#94a3b8"
CHART_UP = "#22c55e"
CHART_DOWN = "#f97316"
CHART_EMA9 = "#f59e0b"
CHART_EMA20 = "#38bdf8"
CHART_ANOMALY = "#a3e635"
CHART_LEVEL = "#c084fc"
CHART_ENTRY = "#f8fafc"
CHART_EXIT = "#fde047"
CHART_RISK_FACE = "#7f1d1d"
CHART_RISK_EDGE = "#ef4444"
CHART_PROFIT_FACE = "#14532d"
CHART_PROFIT_EDGE = "#22c55e"
CHART_PROTECT_FACE = "#14532d"
CHART_PROTECT_EDGE = "#86efac"
CHART_BASE_FACE = "#1d4ed8"
CHART_BASE_EDGE = "#60a5fa"
CHART_PANEL_EDGE = "#1e293b"
CHART_CANDLE_WIDTH = 0.64
CHART_5M_CANDLE_WIDTH = 4.0
CHART_MAX_X_TICKS = 8
TRADE_CHART_FIGSIZE = (8.0, 10.0)
CHART_AXIS_TAG_LABEL_WIDTH = 7
CHART_AXIS_TAG_TEXT_WIDTH = 20
CHART_SAVEFIG_KWARGS = {"dpi": 100, "facecolor": CHART_FIGURE_FACE, "pil_kwargs": {"compress_level": 1}}


def resolve_candle_width(x_values: np.ndarray, *, default: float = CHART_CANDLE_WIDTH) -> float:
    finite_x = np.asarray(x_values[np.isfinite(x_values)], dtype=np.float64)
    if finite_x.size < 2:
        return float(default)
    diffs = np.diff(np.sort(np.unique(finite_x)))
    diffs = diffs[diffs > 0.0]
    if diffs.size == 0:
        return float(default)
    spacing_width = min(float(np.median(diffs)) * 0.72, float(np.min(diffs)) * 0.92)
    crowding_scale = min(1.0, 120.0 / max(float(finite_x.size), 1.0))
    adaptive_width = spacing_width * max(crowding_scale, 0.32)
    return max(min(adaptive_width, spacing_width), 0.08)


def draw_candles(
    ax: plt.Axes,
    frame: pd.DataFrame,
    x_values: np.ndarray,
    *,
    up_color: str = CHART_UP,
    down_color: str = CHART_DOWN,
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
    candle_width = resolve_candle_width(x_values)
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


def resolve_plot_index_span(
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


def draw_price_zone(
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
    span = resolve_plot_index_span(
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


def format_price_label(value: float | None) -> str:
    if value is None or not np.isfinite(float(value)):
        return "n/a"
    numeric = float(value)
    if abs(numeric) >= 1000:
        return f"{numeric:,.0f}"
    if abs(numeric) >= 100:
        return f"{numeric:.2f}"
    if abs(numeric) >= 1:
        return f"{numeric:.4f}"
    return f"{numeric:.6f}"


def format_axis_tag_text(label: str, value: float | None) -> str:
    base = f"{label.upper():<{CHART_AXIS_TAG_LABEL_WIDTH}} {format_price_label(value)}"
    return f" {base:<{CHART_AXIS_TAG_TEXT_WIDTH}} "


def annotate_axis_price_tag(
    ax: plt.Axes,
    *,
    y: float | None,
    label: str,
    color: str,
    leader_start_x: float | None = None,
    leader_end_x: float = 1.0,
    text_y: float | None = None,
    alpha: float = 0.88,
    text_color: str = CHART_TEXT,
) -> None:
    if y is None or not np.isfinite(float(y)):
        return
    y_value = float(y)
    label_y = y_value if text_y is None or not np.isfinite(float(text_y)) else float(text_y)
    transform = blended_transform_factory(ax.transAxes, ax.transData)
    ax.text(
        1.006,
        label_y,
        format_axis_tag_text(label, y_value),
        transform=transform,
        va="center",
        ha="left",
        fontsize=7.2,
        color=text_color,
        bbox={
            "boxstyle": "round,pad=0.18,rounding_size=0.06",
            "facecolor": CHART_AXIS_FACE,
            "edgecolor": color,
            "linewidth": 0.8,
            "alpha": alpha,
        },
        zorder=9,
        clip_on=False,
    )
    if leader_start_x is None:
        ax.axhline(y_value, color=color, linewidth=0.75, alpha=0.22, zorder=2.8)
        return
    ax.plot(
        [float(leader_start_x), float(leader_end_x)],
        [y_value, label_y],
        color=color,
        linewidth=0.75,
        alpha=0.42,
        zorder=4.2,
        clip_on=False,
    )


def infer_frame_step_ms(frame: pd.DataFrame) -> int | None:
    if "timestamp" not in frame.columns or len(frame) < 2:
        return None
    timestamps = np.asarray(frame["timestamp"].to_numpy(dtype=np.float64), dtype=np.float64)
    diffs = np.diff(np.sort(np.unique(timestamps[np.isfinite(timestamps)])))
    diffs = diffs[diffs > 0]
    if diffs.size == 0:
        return None
    return int(round(float(np.median(diffs))))


def format_chart_symbol(symbol: str) -> str:
    candidate = symbol.strip().upper()
    if "/" in candidate:
        candidate = candidate.split("/", 1)[0]
    candidate = re.sub(r"[^A-Z0-9]", "", candidate)
    for suffix in ("USDT", "USDC", "BUSD", "USD"):
        if candidate.endswith(suffix) and len(candidate) > len(suffix):
            return candidate[: -len(suffix)]
    return candidate or symbol


def format_timeframe_label(step_ms: int | None) -> str:
    if step_ms is None or step_ms <= 0:
        return "Price"
    seconds = step_ms / 1000.0
    if seconds < 60:
        return f"{seconds:g}s"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:g}m"
    hours = minutes / 60.0
    return f"{hours:g}h"


def resolve_axis_tag_positions(label_values: list[tuple[str, float | None]]) -> dict[str, float]:
    finite = [(label, float(value)) for label, value in label_values if value is not None and np.isfinite(float(value))]
    if not finite:
        return {}
    finite.sort(key=lambda item: item[1])
    values = [value for _label, value in finite]
    min_gap = max((max(values) - min(values)) * 0.055, max(abs(value) for value in values) * 0.00075, 1e-9)
    resolved: dict[str, float] = {}
    last_y = -np.inf
    for label, value in finite:
        y = max(value, last_y + min_gap)
        resolved[label] = y
        last_y = y
    return resolved


def configure_plot_axes(*, price_ax: plt.Axes, volume_ax: plt.Axes, trades_ax: plt.Axes | None = None) -> None:
    axes = [price_ax, volume_ax]
    if trades_ax is not None:
        axes.append(trades_ax)
    for axis in axes:
        axis.set_facecolor(CHART_AXIS_FACE)
        axis.grid(True, color=CHART_GRID, linewidth=0.7, alpha=0.18)
        axis.tick_params(axis="both", colors=CHART_MUTED, labelsize=8, length=0, pad=6)
        for spine in axis.spines.values():
            spine.set_color(CHART_PANEL_EDGE)
            spine.set_linewidth(0.8)
    price_ax.yaxis.label.set_color(CHART_MUTED)
    volume_ax.yaxis.label.set_color(CHART_MUTED)
    if trades_ax is not None:
        trades_ax.yaxis.label.set_color(CHART_MUTED)
    price_ax.yaxis.set_major_locator(LinearLocator(7))
    volume_ax.yaxis.set_major_locator(LinearLocator(3))
    if trades_ax is not None:
        trades_ax.yaxis.set_major_locator(LinearLocator(3))


def resolve_timestamp_plot_idx(timestamps: np.ndarray, target_timestamp_ms: int) -> int:
    if timestamps.size == 0:
        return 0
    idx = int(np.searchsorted(timestamps, int(target_timestamp_ms), side="left"))
    if idx <= 0:
        return 0
    if idx >= len(timestamps):
        return len(timestamps) - 1
    previous_distance = abs(int(target_timestamp_ms) - int(timestamps[idx - 1]))
    current_distance = abs(int(timestamps[idx]) - int(target_timestamp_ms))
    return idx - 1 if previous_distance <= current_distance else idx


def draw_candles_on_columns(
    ax: plt.Axes,
    frame: pd.DataFrame,
    *,
    x_column: str,
    candle_width: float,
) -> None:
    if frame.empty or x_column not in frame.columns:
        return
    x_values = frame[x_column].to_numpy(dtype=np.float64)
    opens = frame["open"].to_numpy(dtype=np.float64)
    highs = frame["high"].to_numpy(dtype=np.float64)
    lows = frame["low"].to_numpy(dtype=np.float64)
    closes = frame["close"].to_numpy(dtype=np.float64)
    valid_mask = np.isfinite(x_values) & np.isfinite(opens) & np.isfinite(highs) & np.isfinite(lows) & np.isfinite(closes)
    if not bool(np.any(valid_mask)):
        return
    x_values = x_values[valid_mask]
    opens = opens[valid_mask]
    highs = highs[valid_mask]
    lows = lows[valid_mask]
    closes = closes[valid_mask]
    up_mask = closes >= opens
    resolved_width = resolve_candle_width(x_values, default=candle_width)
    wick_segments = np.stack(
        [
            np.column_stack([x_values, lows]),
            np.column_stack([x_values, highs]),
        ],
        axis=1,
    )
    wick_colors = np.where(up_mask, CHART_UP, CHART_DOWN)
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


def build_tick_timestamps(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(frame["timestamp"].astype("int64"), unit="ms", utc=True)


def build_tick_positions_from_timestamps(timestamps: pd.Series, frame_length: int) -> np.ndarray:
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
    if len(selected) > CHART_MAX_X_TICKS:
        source = list(selected)
        selected = list(np.unique(np.linspace(0, len(source) - 1, CHART_MAX_X_TICKS, dtype=int)))
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


def build_tick_labels_from_timestamps(timestamps: pd.Series, positions: np.ndarray) -> list[str]:
    selected = pd.DatetimeIndex(timestamps).take(positions)
    full_day_mask = (selected.hour == 0) & (selected.minute == 0)
    day_labels = selected.strftime("%m-%d")
    time_labels = selected.strftime("%H:%M")
    return np.where(full_day_mask, day_labels, time_labels).tolist()
