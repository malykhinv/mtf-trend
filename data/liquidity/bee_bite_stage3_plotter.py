"""Matplotlib plotter for bee_bite stage-3 review."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from data.liquidity.bee_bite_stage1_selector import BeeBiteStage1Result
from strategy.bee_bite.stage2_detector import BeeBiteStage2Result
from strategy.bee_bite.stage3_detector import BeeBiteStage3Result


class BeeBiteStage3Plotter:
    _CANDLE_WIDTH = 0.65
    _PRE_PUMP_CONTEXT_BARS = 12
    _POST_CONTEXT_BARS = 20
    _FIGURE_FACE = "#08111f"
    _AXIS_FACE = "#0f172a"
    _GRID_COLOR = "#334155"
    _TEXT_COLOR = "#e2e8f0"
    _UP_COLOR = "#22c55e"
    _DOWN_COLOR = "#f97316"
    _BOX_EDGE = "#34d399"
    _BOX_FACE = "#064e3b"
    _CURRENT_BOX_EDGE = "#22d3ee"
    _LOWER_ZONE_EDGE = "#c4b5fd"
    _LOWER_ZONE_FACE = "#4c1d95"
    _BREAK_COLOR = "#ef4444"
    _RECLAIM_COLOR = "#38bdf8"
    _HOLD_COLOR = "#f59e0b"
    _START_COLOR = "#38bdf8"
    _PEAK_COLOR = "#fb7185"
    _STAGE1_COLOR = "#a3e635"

    def plot_result(
        self,
        *,
        frame: pd.DataFrame,
        stage1_event: BeeBiteStage1Result,
        stage2_result: BeeBiteStage2Result,
        stage3_result: BeeBiteStage3Result,
        output_path: Path,
        window_end_timestamp: int | None = None,
        title_suffix: str | None = None,
        dpi: int = 160,
        include_volume: bool = True,
    ) -> None:
        required_columns = {"timestamp", "open", "high", "low", "close", "volume"}
        if frame.empty or not required_columns.issubset(frame.columns):
            raise ValueError("Stage-3 plot requires timestamp/open/high/low/close/volume columns.")

        prepared = frame.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]].copy()
        for column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
        prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
        if prepared.empty:
            raise ValueError("Stage-3 plot received an empty frame after normalization.")

        end_anchor = int(
            window_end_timestamp
            or stage3_result.reclaim_timestamp
            or stage3_result.invalidation_timestamp
            or stage3_result.analysis_end_timestamp
            or stage2_result.analysis_end_timestamp
            or prepared.iloc[-1]["timestamp"]
        )
        pump_start_idx = self._timestamp_to_index(prepared, stage1_event.pump_start_timestamp)
        end_idx = self._timestamp_to_index(prepared, end_anchor)
        window_start = max(0, pump_start_idx - self._PRE_PUMP_CONTEXT_BARS)
        post_context_bars = 0 if window_end_timestamp is not None else self._POST_CONTEXT_BARS
        window_end = min(len(prepared) - 1, end_idx + post_context_bars)
        window = prepared.iloc[window_start : window_end + 1].reset_index(drop=True)
        x_positions = list(range(len(window)))

        if include_volume:
            fig, (price_ax, volume_ax) = plt.subplots(
                2,
                1,
                figsize=(16, 9),
                sharex=True,
                gridspec_kw={"height_ratios": [4, 1]},
                facecolor=self._FIGURE_FACE,
            )
        else:
            fig, price_ax = plt.subplots(
                1,
                1,
                figsize=(16, 7),
                facecolor=self._FIGURE_FACE,
            )
            volume_ax = None
        price_ax.set_facecolor(self._AXIS_FACE)
        axes = [price_ax]
        if volume_ax is not None:
            volume_ax.set_facecolor(self._AXIS_FACE)
            axes.append(volume_ax)
        for axis in axes:
            axis.tick_params(colors=self._TEXT_COLOR)
            for spine in axis.spines.values():
                spine.set_color(self._GRID_COLOR)

        self._draw_candles(price_ax, window, x_positions)
        if volume_ax is not None:
            self._draw_volume(volume_ax, window, x_positions)

        self._draw_marker(
            price_ax,
            pump_start_idx - window_start,
            float(stage1_event.pump_base_price or prepared.iloc[pump_start_idx]["low"]),
            self._START_COLOR,
            "pump start",
        )
        if stage1_event.pump_peak_timestamp is not None and stage1_event.pump_peak_price is not None:
            pump_peak_idx = self._timestamp_to_index(prepared, stage1_event.pump_peak_timestamp)
            self._draw_marker(
                price_ax,
                pump_peak_idx - window_start,
                float(stage1_event.pump_peak_price),
                self._PEAK_COLOR,
                "stage1 peak",
            )
        if stage1_event.stage1_confirmed_timestamp is not None:
            stage1_confirmed_idx = self._timestamp_to_index(prepared, stage1_event.stage1_confirmed_timestamp)
            self._draw_marker(
                price_ax,
                stage1_confirmed_idx - window_start,
                float(prepared.iloc[stage1_confirmed_idx]["close"]),
                self._STAGE1_COLOR,
                "stage1 confirmed",
            )

        if stage3_result.hold_price is not None:
            price_ax.axhline(
                float(stage3_result.hold_price),
                color=self._HOLD_COLOR,
                linestyle="--",
                linewidth=1.2,
                alpha=0.9,
                label="0.4 hold",
            )

        reference_box_start_timestamp = (
            stage3_result.reference_box_start_timestamp
            if stage3_result.reference_box_start_timestamp is not None
            else stage2_result.box_start_timestamp
        )
        reference_box_low = (
            float(stage3_result.box_low)
            if stage3_result.box_low is not None
            else float(stage2_result.box_low or 0.0)
        )
        reference_box_high = (
            float(stage3_result.box_high)
            if stage3_result.box_high is not None
            else float(stage2_result.box_high or 0.0)
        )
        if reference_box_start_timestamp is not None and reference_box_high > reference_box_low:
            self._draw_rectangle(
                axis=price_ax,
                start_idx=self._timestamp_to_index(prepared, int(reference_box_start_timestamp)),
                end_idx=window_end,
                visible_start_idx=window_start,
                visible_end_idx=window_end,
                low=reference_box_low,
                high=reference_box_high,
                index_shift=window_start,
                edge_color=self._BOX_EDGE,
                face_color=self._BOX_FACE,
                alpha=0.16,
                label="stage2 box",
            )
        if (
            stage2_result.box_start_timestamp is not None
            and stage2_result.box_low is not None
            and stage2_result.box_high is not None
            and (
                stage2_result.box_start_timestamp != reference_box_start_timestamp
                or abs(float(stage2_result.box_low) - reference_box_low) > 1e-12
                or abs(float(stage2_result.box_high) - reference_box_high) > 1e-12
            )
        ):
            self._draw_rectangle(
                axis=price_ax,
                start_idx=self._timestamp_to_index(prepared, int(stage2_result.box_start_timestamp)),
                end_idx=window_end,
                visible_start_idx=window_start,
                visible_end_idx=window_end,
                low=float(stage2_result.box_low),
                high=float(stage2_result.box_high),
                index_shift=window_start,
                edge_color=self._CURRENT_BOX_EDGE,
                face_color="none",
                alpha=0.45,
                label="current stage2 box",
                line_style="--",
            )

        lower_zone = stage3_result.active_lower_liquidity_zone
        if lower_zone is not None:
            lower_zone_end_idx = window_end
            if stage3_result.break_idx is not None:
                lower_zone_end_idx = max(lower_zone.start_idx, stage3_result.break_idx - 1)
            self._draw_rectangle(
                axis=price_ax,
                start_idx=lower_zone.start_idx,
                end_idx=lower_zone_end_idx,
                visible_start_idx=window_start,
                visible_end_idx=window_end,
                low=lower_zone.low,
                high=lower_zone.high,
                index_shift=window_start,
                edge_color=self._LOWER_ZONE_EDGE,
                face_color=self._LOWER_ZONE_FACE,
                alpha=0.22,
                label="active lower liquidity",
            )

        if stage3_result.lowest_break_idx is not None and stage3_result.lowest_break_price is not None:
            self._draw_marker(
                price_ax,
                stage3_result.lowest_break_idx - window_start,
                float(stage3_result.lowest_break_price),
                self._BREAK_COLOR,
                "sweep low",
            )
        if stage3_result.reclaim_idx is not None and stage3_result.box_low is not None:
            self._draw_marker(
                price_ax,
                stage3_result.reclaim_idx - window_start,
                float(stage3_result.box_low),
                self._RECLAIM_COLOR,
                "reclaim close",
            )
        if stage3_result.invalidation_idx is not None and stage3_result.hold_price is not None:
            self._draw_marker(
                price_ax,
                stage3_result.invalidation_idx - window_start,
                float(stage3_result.hold_price),
                self._BREAK_COLOR,
                "hold invalidation",
            )

        title = f"{stage1_event.symbol} | stage3"
        if title_suffix:
            title = f"{title} | {title_suffix}"
        price_ax.set_title(title)
        price_ax.title.set_color(self._TEXT_COLOR)
        price_ax.set_ylabel("Price", color=self._TEXT_COLOR)
        price_ax.grid(alpha=0.18, color=self._GRID_COLOR)

        info_lines = [
            f"Stage-3: {'pass' if stage3_result.passed else 'fail'} ({stage3_result.reason})",
            f"Bars under range: {int(stage3_result.bars_under_range or 0)}",
        ]
        if stage3_result.under_range_span_pct is not None:
            info_lines.append(f"Under-range span: {stage3_result.under_range_span_pct * 100:.2f}%")
        if stage3_result.range_size_pct is not None:
            info_lines.append(f"Box size: {stage3_result.range_size_pct * 100:.2f}%")
        if (
            stage2_result.box_low is not None
            and stage2_result.box_high is not None
            and (
                abs(float(stage2_result.box_low) - reference_box_low) > 1e-12
                or abs(float(stage2_result.box_high) - reference_box_high) > 1e-12
            )
        ):
            info_lines.append(f"Current stage2: {float(stage2_result.box_low):.5f} .. {float(stage2_result.box_high):.5f}")
            info_lines.append(f"Anchor box: {reference_box_low:.5f} .. {reference_box_high:.5f}")
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

        tick_positions = self._build_tick_positions(window)
        tick_labels = self._build_tick_labels(window, tick_positions)
        if volume_ax is not None:
            volume_ax.set_ylabel("Volume", color=self._TEXT_COLOR)
            volume_ax.grid(alpha=0.18, color=self._GRID_COLOR)
            volume_ax.set_xticks(tick_positions)
            volume_ax.set_xticklabels(tick_labels, rotation=0, ha="center", color=self._TEXT_COLOR)
        else:
            price_ax.set_xticks(tick_positions)
            price_ax.set_xticklabels(tick_labels, rotation=0, ha="center", color=self._TEXT_COLOR)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.tight_layout()
        fig.savefig(output_path, dpi=max(int(dpi), 72))
        plt.close(fig)

    @staticmethod
    def _draw_rectangle(
        *,
        axis,
        start_idx: int,
        end_idx: int,
        visible_start_idx: int,
        visible_end_idx: int,
        low: float,
        high: float,
        index_shift: int,
        edge_color: str,
        face_color: str,
        alpha: float,
        label: str,
        line_style: str = "-",
    ) -> None:
        clipped_start_idx = max(start_idx, visible_start_idx)
        clipped_end_idx = min(end_idx, visible_end_idx)
        if clipped_end_idx < clipped_start_idx:
            return
        x0 = clipped_start_idx - index_shift - 0.5
        width = (clipped_end_idx - clipped_start_idx) + 1.0
        axis.add_patch(
            Rectangle(
                (x0, low),
                width,
                max(high - low, 1e-12),
                facecolor=face_color,
                edgecolor=edge_color,
                linewidth=1.0,
                alpha=alpha,
                label=label,
                linestyle=line_style,
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

    @staticmethod
    def _draw_marker(axis, x_idx: int, price: float, color: str, label: str) -> None:
        axis.scatter(x_idx, price, color=color, s=55, zorder=6, label=label)
        axis.axvline(x_idx, color=color, linewidth=0.9, alpha=0.25)

    @staticmethod
    def _timestamp_to_index(frame: pd.DataFrame, timestamp: int) -> int:
        matches = frame.index[frame["timestamp"].astype("int64") == int(timestamp)]
        if len(matches) == 0:
            raise ValueError(f"Timestamp {timestamp} was not found in the plotting frame.")
        return int(matches[0])

    @staticmethod
    def _build_tick_positions(frame: pd.DataFrame) -> list[int]:
        timestamps = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        positions = [idx for idx, ts in enumerate(timestamps) if ts.minute == 0]
        if len(positions) < 2:
            positions = [idx for idx, ts in enumerate(timestamps) if ts.minute in {0, 30}]
        if not positions:
            return [0, len(frame) - 1] if len(frame) > 1 else [0]
        if len(positions) > 8:
            step = max(1, len(positions) // 8)
            positions = positions[::step]
            if positions[-1] != len(frame) - 1 and timestamps.iloc[-1].minute == 0:
                positions.append(len(frame) - 1)
        return positions

    @staticmethod
    def _build_tick_labels(frame: pd.DataFrame, positions: list[int]) -> list[str]:
        timestamps = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        return [timestamps.iloc[idx].strftime("%m-%d %H:%M") for idx in positions]
