"""Headless renderer for strategy-neutral chart specifications."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.patches import Rectangle
from matplotlib.transforms import blended_transform_factory

from anomaly_science.visualization.contracts import ChartSpec
from anomaly_science.visualization.theme import ChartTheme, DEFAULT_THEME


def _nearest_index(timestamps: np.ndarray, target: int) -> int:
    index = int(np.searchsorted(timestamps, target, side="left"))
    if index <= 0:
        return 0
    if index >= len(timestamps):
        return len(timestamps) - 1
    return index - 1 if target - timestamps[index - 1] <= timestamps[index] - target else index


def _candle_width(count: int) -> float:
    return 0.64 * max(min(120.0 / max(count, 1), 1.0), 0.34)


def _draw_candles(axis: plt.Axes, spec: ChartSpec, theme: ChartTheme) -> None:
    candles = spec.candles
    x = np.arange(len(candles.close), dtype=float)
    rising = candles.close >= candles.open
    colors = np.where(rising, theme.up, theme.down)
    segments = np.stack((np.column_stack((x, candles.low)), np.column_stack((x, candles.high))), axis=1)
    axis.add_collection(LineCollection(segments, colors=colors.tolist(), linewidths=0.9, zorder=3))
    bottoms = np.minimum(candles.open, candles.close)
    heights = np.maximum(np.abs(candles.close - candles.open), 1e-12)
    width = _candle_width(len(x))
    patches = [
        Rectangle((position - width / 2, bottom), width, height)
        for position, bottom, height in zip(x, bottoms, heights, strict=True)
    ]
    axis.add_collection(
        PatchCollection(
            patches,
            facecolor=colors.tolist(),
            edgecolor=colors.tolist(),
            linewidth=0.7,
            zorder=4,
        )
    )


def _configure_axis(axis: plt.Axes, theme: ChartTheme) -> None:
    axis.set_facecolor(theme.axis_face)
    axis.grid(True, color=theme.grid, linewidth=0.65, alpha=0.17)
    axis.tick_params(axis="both", colors=theme.muted, labelsize=8, length=0, pad=5)
    for spine in axis.spines.values():
        spine.set_color(theme.panel_edge)
        spine.set_linewidth(0.8)


def _draw_price_overlays(axis: plt.Axes, spec: ChartSpec, theme: ChartTheme) -> None:
    timestamps = spec.candles.timestamps_ms.astype(np.int64)
    count = len(timestamps)
    for zone in spec.zones:
        start = _nearest_index(timestamps, zone.start_timestamp_ms)
        end = _nearest_index(timestamps, zone.end_timestamp_ms)
        axis.add_patch(
            Rectangle(
                (start - 0.45, min(zone.low, zone.high)),
                max(end - start + 0.9, 0.9),
                max(abs(zone.high - zone.low), 1e-12),
                facecolor=zone.face_color,
                edgecolor=zone.edge_color,
                linewidth=0.9,
                alpha=zone.alpha,
                zorder=1.5,
            )
        )
    for line in spec.lines:
        if len(line.values) != count:
            raise ValueError(f"line series {line.label!r} must align with candle count")
        axis.plot(
            np.arange(count), line.values, color=line.color, linewidth=line.linewidth,
            alpha=line.alpha, label=line.label or None, zorder=5,
        )
    for level in spec.levels:
        start = 0 if level.start_timestamp_ms is None else _nearest_index(timestamps, level.start_timestamp_ms)
        end = count - 1 if level.end_timestamp_ms is None else _nearest_index(timestamps, level.end_timestamp_ms)
        axis.plot(
            [start, end], [level.price, level.price], color=level.color,
            linestyle=level.linestyle, linewidth=level.linewidth, alpha=level.alpha, zorder=2.5,
        )
        if level.label:
            transform = blended_transform_factory(axis.transAxes, axis.transData)
            axis.text(
                1.006, level.price, f" {level.label}  {level.price:g} ", transform=transform,
                va="center", ha="left", fontsize=7, color=theme.text,
                bbox={"boxstyle": "round,pad=0.15", "facecolor": theme.axis_face,
                      "edgecolor": level.color, "linewidth": 0.7, "alpha": 0.9},
                clip_on=False, zorder=9,
            )
    for swing in spec.swings:
        index = _nearest_index(timestamps, swing.timestamp_ms)
        color = theme.swing_high if swing.kind == "high" else theme.swing_low
        marker = "v" if swing.kind == "high" else "^"
        axis.scatter(index, swing.price, marker=marker, s=18, color=color, alpha=0.85, zorder=6)
    for marker in spec.markers:
        index = _nearest_index(timestamps, marker.timestamp_ms)
        axis.scatter(
            index, marker.price, marker=marker.marker, s=marker.size,
            color=marker.color, label=marker.label or None, zorder=7,
        )


def _draw_histograms(axis: plt.Axes, spec: ChartSpec, theme: ChartTheme) -> None:
    count = len(spec.candles.close)
    width = _candle_width(count)
    for series in spec.histograms:
        values = np.asarray(series.values, dtype=float)
        if len(values) != count:
            raise ValueError(f"histogram {series.label!r} must align with candle count")
        if series.normalize:
            scale = float(np.nanmax(np.abs(values))) if len(values) else 0.0
            values = values / scale * 100.0 if scale > 0 else np.zeros_like(values)
        axis.bar(
            np.arange(count), values, width=width, color=series.color,
            alpha=series.alpha, label=series.label or None,
        )
    if spec.histograms:
        legend = axis.legend(loc="upper left", frameon=False, fontsize=7, ncol=3)
        for text in legend.get_texts():
            text.set_color(theme.muted)


def _apply_time_ticks(axis: plt.Axes, timestamps_ms: np.ndarray, theme: ChartTheme) -> None:
    count = len(timestamps_ms)
    positions = np.unique(np.linspace(0, count - 1, min(8, count), dtype=int))
    timestamps = pd.to_datetime(timestamps_ms[positions], unit="ms", utc=True)
    labels = [stamp.strftime("%m-%d") if stamp.hour == 0 and stamp.minute == 0 else stamp.strftime("%H:%M") for stamp in timestamps]
    axis.set_xticks(positions)
    axis.set_xticklabels(labels, color=theme.muted)


def render_chart(
    spec: ChartSpec,
    output_path: Path,
    *,
    theme: ChartTheme = DEFAULT_THEME,
    figsize: tuple[float, float] = (12.8, 7.2),
    dpi: int = 120,
) -> Path:
    """Render one immutable chart specification to PNG."""
    histogram_rows = 1 if spec.histograms else 0
    fig, axes = plt.subplots(
        1 + histogram_rows,
        1,
        sharex=True,
        figsize=figsize,
        facecolor=theme.figure_face,
        gridspec_kw={"height_ratios": [4.5, 1.0]} if histogram_rows else None,
        squeeze=False,
    )
    price_axis = axes[0, 0]
    histogram_axis = axes[1, 0] if histogram_rows else None
    _configure_axis(price_axis, theme)
    if histogram_axis is not None:
        _configure_axis(histogram_axis, theme)
    _draw_candles(price_axis, spec, theme)
    _draw_price_overlays(price_axis, spec, theme)
    if histogram_axis is not None:
        _draw_histograms(histogram_axis, spec, theme)
    price_axis.set_xlim(-1, len(spec.candles.close))
    price_axis.set_ylabel("Price", color=theme.muted, fontsize=8)
    price_axis.set_title(spec.title, color=theme.text, fontsize=12, loc="left", pad=10)
    if spec.subtitle_lines:
        price_axis.text(
            0.012, 0.975, "\n".join(spec.subtitle_lines), transform=price_axis.transAxes,
            va="top", ha="left", color=theme.text, fontsize=8,
            bbox={"boxstyle": "round,pad=0.35", "facecolor": theme.figure_face,
                  "edgecolor": theme.panel_edge, "alpha": 0.86}, zorder=10,
        )
    tick_axis = histogram_axis if histogram_axis is not None else price_axis
    _apply_time_ticks(tick_axis, spec.candles.timestamps_ms, theme)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(left=0.065, right=0.88, top=0.93, bottom=0.08, hspace=0.04)
    fig.savefig(output_path, dpi=dpi, facecolor=theme.figure_face, pil_kwargs={"compress_level": 2})
    plt.close(fig)
    return output_path


__all__ = ["render_chart"]
