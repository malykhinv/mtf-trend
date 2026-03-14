"""Matplotlib plotter for bee_bite stage-2 review."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from data.liquidity.bee_bite_stage1_selector import BeeBiteStage1Result
from strategy.bee_bite.stage2_detector import BeeBiteStage2LiquidityZone, BeeBiteStage2Result


class BeeBiteStage2Plotter:
    """Render a stage-2 review chart with detected local and merged balances."""

    _CANDLE_WIDTH = 0.65
    _PRE_CONTEXT_BARS = 24
    _FIGURE_FACE = "#08111f"
    _AXIS_FACE = "#0f172a"
    _GRID_COLOR = "#334155"
    _TEXT_COLOR = "#e2e8f0"
    _UP_COLOR = "#22c55e"
    _DOWN_COLOR = "#f97316"
    _START_COLOR = "#38bdf8"
    _PEAK_COLOR = "#fb7185"
    _STAGE1_COLOR = "#a3e635"
    _CONFIRMED_HIGH_COLOR = "#f43f5e"
    _HOLD_COLOR = "#f59e0b"
    _LOCAL_RANGE_EDGE = "#60a5fa"
    _LOCAL_RANGE_FACE = "#1d4ed8"
    _MERGED_RANGE_EDGE = "#34d399"
    _UPPER_ZONE_EDGE = "#fca5a5"
    _UPPER_ZONE_FACE = "#7f1d1d"
    _LOWER_ZONE_EDGE = "#c4b5fd"
    _LOWER_ZONE_FACE = "#4c1d95"
    _LOCAL_RANGE_ALPHA = 0.05
    _MERGED_RANGE_ALPHA = 0.28
    _CROSSED_ZONE_ALPHA = 0.16
    _ACTIVE_ZONE_ALPHA = 0.3

    def plot_result(
        self,
        *,
        frame: pd.DataFrame,
        stage1_event: BeeBiteStage1Result,
        stage2_result: BeeBiteStage2Result,
        output_path: Path,
        window_end_timestamp: int | None = None,
        title_suffix: str | None = None,
    ) -> None:
        required_columns = {"timestamp", "open", "high", "low", "close", "volume"}
        if frame.empty or not required_columns.issubset(frame.columns):
            raise ValueError("Stage-2 plot requires timestamp/open/high/low/close/volume columns.")

        prepared = frame.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]].copy()
        for column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
        prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
        if prepared.empty:
            raise ValueError("Stage-2 plot received an empty frame after normalization.")

        pump_start_idx = self._timestamp_to_index(prepared, stage1_event.pump_start_timestamp)
        default_end_ts = window_end_timestamp if window_end_timestamp is not None else stage2_result.analysis_end_timestamp
        window_end_idx = self._timestamp_to_index(prepared, default_end_ts)
        window_start = max(0, pump_start_idx - self._PRE_CONTEXT_BARS)
        window_end = min(len(prepared) - 1, window_end_idx)
        window = prepared.iloc[window_start : window_end + 1].reset_index(drop=True)
        x_positions = list(range(len(window)))

        fig, (price_ax, volume_ax) = plt.subplots(
            2,
            1,
            figsize=(16, 9),
            sharex=True,
            gridspec_kw={"height_ratios": [4, 1]},
            facecolor=self._FIGURE_FACE,
        )
        price_ax.set_facecolor(self._AXIS_FACE)
        volume_ax.set_facecolor(self._AXIS_FACE)
        for axis in (price_ax, volume_ax):
            axis.tick_params(colors=self._TEXT_COLOR)
            for spine in axis.spines.values():
                spine.set_color(self._GRID_COLOR)

        self._draw_candles(price_ax, window, x_positions)
        self._draw_volume(volume_ax, window, x_positions)

        if stage1_event.hold_price is not None:
            price_ax.axhline(
                float(stage1_event.hold_price),
                color=self._HOLD_COLOR,
                linestyle="--",
                linewidth=1.2,
                alpha=0.9,
                label="0.5 hold",
            )

        for local_range in stage2_result.local_ranges:
            self._draw_range_rectangle(
                axis=price_ax,
                range_start_idx=local_range.start_idx,
                range_end_idx=local_range.end_idx,
                range_low=local_range.low,
                range_high=local_range.high,
                index_shift=window_start,
                edge_color=self._LOCAL_RANGE_EDGE,
                face_color=self._LOCAL_RANGE_FACE,
                alpha=self._LOCAL_RANGE_ALPHA,
                line_width=0.8,
            )

        for merged_range in stage2_result.merged_ranges:
            self._draw_range_rectangle(
                axis=price_ax,
                range_start_idx=merged_range.start_idx,
                range_end_idx=merged_range.end_idx,
                range_low=merged_range.low,
                range_high=merged_range.high,
                index_shift=window_start,
                edge_color=self._MERGED_RANGE_EDGE,
                face_color="none",
                alpha=self._MERGED_RANGE_ALPHA,
                line_width=1.0,
            )

        for index, liquidity_zone in enumerate(stage2_result.liquidity_zones):
            is_upper = liquidity_zone.side == "upper"
            is_active = (
                stage2_result.box_end_timestamp is not None
                and liquidity_zone.end_timestamp >= int(stage2_result.box_end_timestamp)
            )
            self._draw_range_rectangle(
                axis=price_ax,
                range_start_idx=liquidity_zone.start_idx,
                range_end_idx=liquidity_zone.end_idx,
                range_low=liquidity_zone.low,
                range_high=liquidity_zone.high,
                index_shift=window_start,
                edge_color=self._UPPER_ZONE_EDGE if is_upper else self._LOWER_ZONE_EDGE,
                face_color=self._UPPER_ZONE_FACE if is_upper else self._LOWER_ZONE_FACE,
                alpha=self._ACTIVE_ZONE_ALPHA if is_active else self._CROSSED_ZONE_ALPHA,
                line_width=1.0,
                label=("upper stop zone" if is_upper else "lower stop zone") if index < 2 else None,
                line_style="--",
            )

        self._draw_marker(
            price_ax,
            pump_start_idx - window_start,
            float(stage1_event.pump_base_price or prepared.iloc[pump_start_idx]["low"]),
            self._START_COLOR,
            "pump start",
        )
        if stage1_event.pump_peak_timestamp is not None and stage1_event.pump_peak_price is not None:
            peak_idx = self._timestamp_to_index(prepared, stage1_event.pump_peak_timestamp)
            self._draw_marker(
                price_ax,
                peak_idx - window_start,
                float(stage1_event.pump_peak_price),
                self._PEAK_COLOR,
                "stage1 peak",
            )
        if stage1_event.stage1_confirmed_timestamp is not None:
            confirmed_idx = self._timestamp_to_index(prepared, stage1_event.stage1_confirmed_timestamp)
            self._draw_marker(
                price_ax,
                confirmed_idx - window_start,
                float(prepared.iloc[confirmed_idx]["close"]),
                self._STAGE1_COLOR,
                "stage1 confirmed",
            )

        for index, confirmed_high in enumerate(stage2_result.confirmed_highs):
            label = "confirmed high" if index == 0 else None
            price_ax.scatter(
                confirmed_high.idx - window_start,
                confirmed_high.price,
                color=self._CONFIRMED_HIGH_COLOR,
                s=42,
                marker="o",
                zorder=7,
                label=label,
            )

        title = f"{stage1_event.symbol} | stage2"
        if title_suffix:
            title = f"{title} | {title_suffix}"
        price_ax.set_title(title)
        price_ax.title.set_color(self._TEXT_COLOR)
        price_ax.set_ylabel("Price", color=self._TEXT_COLOR)
        price_ax.grid(alpha=0.18, color=self._GRID_COLOR)

        info_lines = [
            f"Confirmed highs: {len(stage2_result.confirmed_highs)}",
            f"Local ranges: {len(stage2_result.local_ranges)}",
            f"Merged ranges: {len(stage2_result.merged_ranges)}",
        ]
        upper_zones = sum(1 for zone in stage2_result.liquidity_zones if zone.side == "upper")
        lower_zones = sum(1 for zone in stage2_result.liquidity_zones if zone.side == "lower")
        info_lines.append(f"Stop zones: U{upper_zones} / L{lower_zones}")
        if stage2_result.box_high is not None and stage2_result.box_low is not None:
            info_lines.append(f"Box: {stage2_result.box_low:.5f} .. {stage2_result.box_high:.5f}")
        price_ax.text(
            0.015,
            0.985,
            "\n".join(info_lines),
            transform=price_ax.transAxes,
            va="top",
            ha="left",
            color=self._TEXT_COLOR,
            fontsize=11,
            bbox={
                "boxstyle": "round,pad=0.45",
                "facecolor": "#111827",
                "edgecolor": self._GRID_COLOR,
                "alpha": 0.95,
            },
        )
        legend = price_ax.legend(
            loc="upper left",
            bbox_to_anchor=(0.015, 0.83),
            frameon=True,
            facecolor="#111827",
            edgecolor=self._GRID_COLOR,
            fontsize=10,
        )
        for text in legend.get_texts():
            text.set_color(self._TEXT_COLOR)

        volume_ax.set_ylabel("Volume", color=self._TEXT_COLOR)
        volume_ax.grid(alpha=0.18, color=self._GRID_COLOR)
        tick_positions = self._build_tick_positions(len(window))
        tick_labels = self._build_tick_labels(window, tick_positions)
        volume_ax.set_xticks(tick_positions)
        volume_ax.set_xticklabels(tick_labels, rotation=0, ha="center", color=self._TEXT_COLOR)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.tight_layout()
        fig.savefig(output_path, dpi=160)
        plt.close(fig)

    def _draw_range_rectangle(
        self,
        *,
        axis,
        range_start_idx: int,
        range_end_idx: int,
        range_low: float,
        range_high: float,
        index_shift: int,
        edge_color: str,
        face_color: str,
        alpha: float,
        line_width: float,
        label: str | None = None,
        line_style: str = "-",
    ) -> None:
        x0 = range_start_idx - index_shift - 0.5
        width = (range_end_idx - range_start_idx) + 1.0
        axis.add_patch(
            Rectangle(
                (x0, range_low),
                width,
                max(range_high - range_low, 1e-12),
                facecolor=face_color,
                edgecolor=edge_color,
                linewidth=line_width,
                alpha=alpha,
                linestyle=line_style,
                label=label,
                zorder=1,
            )
        )

    def _draw_candles(self, axis, frame: pd.DataFrame, x_positions: list[int]) -> None:
        for idx, row in zip(x_positions, frame.itertuples(index=False), strict=False):
            open_price = float(row.open)
            high_price = float(row.high)
            low_price = float(row.low)
            close_price = float(row.close)
            color = self._UP_COLOR if close_price >= open_price else self._DOWN_COLOR
            axis.vlines(idx, low_price, high_price, color=color, linewidth=1.0, alpha=0.9, zorder=3)
            body_low = min(open_price, close_price)
            body_height = abs(close_price - open_price)
            if body_height == 0:
                axis.hlines(body_low, idx - self._CANDLE_WIDTH / 2, idx + self._CANDLE_WIDTH / 2, color=color, linewidth=1.2, zorder=4)
                continue
            axis.add_patch(
                Rectangle(
                    (idx - self._CANDLE_WIDTH / 2, body_low),
                    self._CANDLE_WIDTH,
                    body_height,
                    facecolor=color,
                    edgecolor=color,
                    linewidth=0.8,
                    alpha=0.85,
                    zorder=4,
                )
            )

    def _draw_volume(self, axis, frame: pd.DataFrame, x_positions: list[int]) -> None:
        colors = [self._UP_COLOR if row.close >= row.open else self._DOWN_COLOR for row in frame.itertuples(index=False)]
        axis.bar(x_positions, frame["volume"], color=colors, width=self._CANDLE_WIDTH, alpha=0.85)

    def _draw_marker(self, axis, x_idx: int, price: float, color: str, label: str) -> None:
        axis.scatter(x_idx, price, color=color, s=55, zorder=6, label=label)
        axis.axvline(x_idx, color=color, linewidth=0.9, alpha=0.25)

    @staticmethod
    def _timestamp_to_index(frame: pd.DataFrame, timestamp: int | None) -> int:
        if timestamp is None:
            raise ValueError("Stage-2 plot requires timestamps.")
        matches = frame.index[frame["timestamp"].astype("int64") == int(timestamp)]
        if len(matches) == 0:
            raise ValueError(f"Timestamp {timestamp} was not found in the plotting frame.")
        return int(matches[0])

    @staticmethod
    def _build_tick_positions(size: int) -> list[int]:
        if size <= 8:
            return list(range(size))
        step = max(1, size // 8)
        positions = list(range(0, size, step))
        if positions[-1] != size - 1:
            positions.append(size - 1)
        return positions

    @staticmethod
    def _build_tick_labels(frame: pd.DataFrame, positions: list[int]) -> list[str]:
        timestamps = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        return [timestamps.iloc[idx].strftime("%m-%d %H:%M") for idx in positions]
