"""Matplotlib plotter for bee_bite stage-4 postmortem."""

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


class BeeBiteStage4Plotter:
    _CANDLE_WIDTH = 0.65
    _PRE_PUMP_CONTEXT_BARS = 12
    _FIGURE_FACE = "#08111f"
    _AXIS_FACE = "#0f172a"
    _GRID_COLOR = "#334155"
    _TEXT_COLOR = "#e2e8f0"
    _UP_COLOR = "#22c55e"
    _DOWN_COLOR = "#f97316"
    _BOX_EDGE = "#34d399"
    _BOX_FACE = "#064e3b"
    _UPPER_ZONE_EDGE = "#fca5a5"
    _UPPER_ZONE_FACE = "#7f1d1d"
    _LOWER_ZONE_EDGE = "#c4b5fd"
    _LOWER_ZONE_FACE = "#4c1d95"
    _ENTRY_COLOR = "#38bdf8"
    _STOP_COLOR = "#ef4444"
    _TP1_COLOR = "#f59e0b"
    _TP2_COLOR = "#eab308"
    _TP3_COLOR = "#22c55e"
    _PEAK_COLOR = "#fb7185"
    _STAGE1_COLOR = "#a3e635"
    _EXIT_COLOR = "#fde047"
    _RISK_FACE = "#7f1d1d"
    _TP1_FACE = "#14532d"
    _TP2_FACE = "#166534"
    _TP3_FACE = "#15803d"
    _FIGURE_SIZE = (8, 6)
    _FIGURE_DPI = 100
    _GRIDSPEC_HEIGHT_RATIOS = [4, 1]

    def plot_result(
        self,
        *,
        frame: pd.DataFrame,
        stage1_event: BeeBiteStage1Result,
        reference_stage2_result: BeeBiteStage2Result,
        stage3_result: BeeBiteStage3Result,
        trade_row: dict[str, object],
        output_path: Path,
        include_volume: bool = True,
        dpi: int = 100,
    ) -> None:
        required_columns = {"timestamp", "open", "high", "low", "close", "volume"}
        if frame.empty or not required_columns.issubset(frame.columns):
            raise ValueError("Stage-4 plot requires timestamp/open/high/low/close/volume columns.")

        prepared = frame.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]].copy()
        for column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
        prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
        if prepared.empty:
            raise ValueError("Stage-4 plot received an empty frame after normalization.")

        end_anchor = int(
            trade_row.get("exit_timestamp")
            or stage3_result.reclaim_timestamp
            or stage3_result.analysis_end_timestamp
            or prepared.iloc[-1]["timestamp"]
        )
        pump_start_idx = self._timestamp_to_index(prepared, stage1_event.pump_start_timestamp)
        end_idx = self._timestamp_to_index(prepared, end_anchor)
        window_start = max(0, pump_start_idx - self._PRE_PUMP_CONTEXT_BARS)
        window_end = min(len(prepared) - 1, end_idx)
        window = prepared.iloc[window_start : window_end + 1].reset_index(drop=True)
        x_positions = list(range(len(window)))

        fig, (price_ax, volume_ax) = plt.subplots(
            2,
            1,
            figsize=self._FIGURE_SIZE,
            sharex=True,
            gridspec_kw={"height_ratios": self._GRIDSPEC_HEIGHT_RATIOS},
            facecolor=self._FIGURE_FACE,
        )

        price_ax.set_facecolor(self._AXIS_FACE)
        volume_ax.set_facecolor(self._AXIS_FACE)
        axes = [price_ax, volume_ax]
        for axis in axes:
            axis.tick_params(colors=self._TEXT_COLOR)
            for spine in axis.spines.values():
                spine.set_color(self._GRID_COLOR)

        self._draw_candles(price_ax, window, x_positions)
        if include_volume:
            self._draw_volume(volume_ax, window, x_positions)
        else:
            volume_ax.set_visible(False)

        box_start_timestamp = (
            int(stage3_result.reference_box_start_timestamp)
            if stage3_result.reference_box_start_timestamp is not None
            else int(reference_stage2_result.box_start_timestamp or prepared.iloc[0]["timestamp"])
        )
        box_low = float(stage3_result.box_low or reference_stage2_result.box_low or 0.0)
        box_high = float(stage3_result.box_high or reference_stage2_result.box_high or 0.0)
        self._draw_rectangle(
            axis=price_ax,
            start_idx=self._timestamp_to_index(prepared, box_start_timestamp),
            end_idx=window_end,
            visible_start_idx=window_start,
            visible_end_idx=window_end,
            low=box_low,
            high=box_high,
            index_shift=window_start,
            edge_color=self._BOX_EDGE,
            face_color=self._BOX_FACE,
            alpha=0.16,
            label="reference box",
        )

        active_upper_zones = [
            zone
            for zone in reference_stage2_result.liquidity_zones
            if zone.side == "upper"
            and reference_stage2_result.box_end_timestamp is not None
            and zone.end_timestamp >= int(reference_stage2_result.box_end_timestamp)
        ]
        for index, zone in enumerate(active_upper_zones):
            self._draw_rectangle(
                axis=price_ax,
                start_idx=zone.start_idx,
                end_idx=window_end,
                visible_start_idx=window_start,
                visible_end_idx=window_end,
                low=zone.low,
                high=zone.high,
                index_shift=window_start,
                edge_color=self._UPPER_ZONE_EDGE,
                face_color=self._UPPER_ZONE_FACE,
                alpha=0.18,
                label="active upper liquidity" if index == 0 else None,
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
                label="crossed lower liquidity",
            )

        entry_price = float(trade_row["entry_price"])
        stop_price = float(trade_row["stop_price"])
        tp1_price = float(trade_row["tp1_price"])
        tp2_price = float(trade_row["tp2_price"])
        tp3_price = float(trade_row["tp3_price"])
        tp2_share = float(trade_row.get("tp2_share", 0.0))
        tp3_share = float(trade_row.get("tp3_share", 0.0))
        entry_timestamp = int(trade_row.get("entry_timestamp") or stage3_result.reclaim_timestamp or prepared.iloc[window_start]["timestamp"])
        exit_timestamp = trade_row.get("exit_timestamp")
        exit_price = trade_row.get("exit_price")
        entry_idx = self._timestamp_to_index(prepared, entry_timestamp)
        exit_idx = (
            self._timestamp_to_index(prepared, int(exit_timestamp))
            if exit_timestamp is not None
            else window_end
        )

        self._draw_trade_band(
            axis=price_ax,
            start_idx=entry_idx,
            end_idx=exit_idx,
            visible_start_idx=window_start,
            visible_end_idx=window_end,
            low=min(stop_price, entry_price),
            high=max(stop_price, entry_price),
            index_shift=window_start,
            face_color=self._RISK_FACE,
            alpha=0.24,
        )
        self._draw_trade_band(
            axis=price_ax,
            start_idx=entry_idx,
            end_idx=exit_idx,
            visible_start_idx=window_start,
            visible_end_idx=window_end,
            low=min(entry_price, tp1_price),
            high=max(entry_price, tp1_price),
            index_shift=window_start,
            face_color=self._TP1_FACE,
            alpha=0.18,
        )
        if tp2_price > tp1_price:
            self._draw_trade_band(
                axis=price_ax,
                start_idx=entry_idx,
                end_idx=exit_idx,
                visible_start_idx=window_start,
                visible_end_idx=window_end,
                low=tp1_price,
                high=tp2_price,
                index_shift=window_start,
                face_color=self._TP2_FACE,
                alpha=0.14,
            )
        if tp3_price > tp2_price:
            self._draw_trade_band(
                axis=price_ax,
                start_idx=entry_idx,
                end_idx=exit_idx,
                visible_start_idx=window_start,
                visible_end_idx=window_end,
                low=tp2_price,
                high=tp3_price,
                index_shift=window_start,
                face_color=self._TP3_FACE,
                alpha=0.12,
            )

        price_ax.axhline(entry_price, color=self._ENTRY_COLOR, linestyle="-", linewidth=1.0, alpha=0.95)
        price_ax.axhline(stop_price, color=self._STOP_COLOR, linestyle="--", linewidth=1.0, alpha=0.95)
        price_ax.axhline(tp1_price, color=self._TP1_COLOR, linestyle="--", linewidth=1.0, alpha=0.95)
        if tp2_share > 0.0:
            price_ax.axhline(tp2_price, color=self._TP2_COLOR, linestyle="--", linewidth=1.0, alpha=0.95)
        if tp3_share > 0.0:
            price_ax.axhline(tp3_price, color=self._TP3_COLOR, linestyle="--", linewidth=1.0, alpha=0.95)
        if bool(trade_row.get("be_armed")):
            price_ax.axhline(entry_price, color=self._ENTRY_COLOR, linestyle=":", linewidth=1.0, alpha=0.9)

        self._draw_vertical_event(price_ax, pump_start_idx - window_start, self._ENTRY_COLOR, alpha=0.55, linewidth=1.0)
        if stage1_event.pump_peak_timestamp is not None and stage1_event.pump_peak_price is not None:
            peak_idx = self._timestamp_to_index(prepared, stage1_event.pump_peak_timestamp)
            self._draw_marker(price_ax, peak_idx - window_start, float(stage1_event.pump_peak_price), self._PEAK_COLOR)
        if stage1_event.stage1_confirmed_timestamp is not None:
            confirmed_idx = self._timestamp_to_index(prepared, stage1_event.stage1_confirmed_timestamp)
            self._draw_vertical_event(price_ax, confirmed_idx - window_start, self._STAGE1_COLOR, alpha=0.35, linewidth=1.0)
        if stage3_result.reclaim_idx is not None:
            self._draw_marker(price_ax, stage3_result.reclaim_idx - window_start, entry_price, self._ENTRY_COLOR)
        if exit_timestamp is not None and exit_price is not None:
            self._draw_marker(price_ax, exit_idx - window_start, float(exit_price), self._EXIT_COLOR)

        title = f"{stage1_event.symbol} | stage4"
        price_ax.set_title(title)
        price_ax.title.set_color(self._TEXT_COLOR)
        price_ax.set_ylabel("Price", color=self._TEXT_COLOR)
        price_ax.grid(alpha=0.18, color=self._GRID_COLOR)

        info_lines = [
            f"Result: {trade_row.get('outcome')}",
            f"RR: {float(trade_row.get('rr') or 0.0):.2f}",
            f"PnL: {float(trade_row.get('realized_pnl_pct') or 0.0):.2f}%",
        ]
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

        tick_positions = self._build_tick_positions(window)
        tick_labels = self._build_tick_labels(window, tick_positions)
        if include_volume:
            volume_ax.set_ylabel("Volume", color=self._TEXT_COLOR)
            volume_ax.grid(alpha=0.18, color=self._GRID_COLOR)
            volume_ax.set_xticks(tick_positions)
            volume_ax.set_xticklabels(tick_labels, rotation=0, ha="center", color=self._TEXT_COLOR)
        else:
            price_ax.set_xticks(tick_positions)
            price_ax.set_xticklabels(tick_labels, rotation=0, ha="center", color=self._TEXT_COLOR)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.subplots_adjust(left=0.07, right=0.985, top=0.93, bottom=0.08, hspace=0.06)
        fig.savefig(output_path, dpi=max(int(dpi), self._FIGURE_DPI))
        plt.close(fig)

    def _draw_candles(self, axis: plt.Axes, frame: pd.DataFrame, positions: list[int]) -> None:
        for position, (_, row) in zip(positions, frame.iterrows(), strict=False):
            open_price = float(row["open"])
            high_price = float(row["high"])
            low_price = float(row["low"])
            close_price = float(row["close"])
            color = self._UP_COLOR if close_price >= open_price else self._DOWN_COLOR
            axis.vlines(position, low_price, high_price, color=color, linewidth=1.2, zorder=2)
            body_bottom = min(open_price, close_price)
            body_height = max(abs(close_price - open_price), 1e-12)
            axis.add_patch(
                Rectangle(
                    (position - self._CANDLE_WIDTH / 2, body_bottom),
                    self._CANDLE_WIDTH,
                    body_height,
                    facecolor=color,
                    edgecolor=color,
                    linewidth=1.0,
                    zorder=3,
                )
            )

    def _draw_volume(self, axis: plt.Axes, frame: pd.DataFrame, positions: list[int]) -> None:
        colors = [self._UP_COLOR if float(row["close"]) >= float(row["open"]) else self._DOWN_COLOR for _, row in frame.iterrows()]
        axis.bar(positions, frame["volume"].astype(float), color=colors, width=self._CANDLE_WIDTH, alpha=0.9)

    @staticmethod
    def _timestamp_to_index(frame: pd.DataFrame, timestamp: int) -> int:
        matches = frame.index[frame["timestamp"].astype("int64") == int(timestamp)]
        if len(matches) == 0:
            raise ValueError(f"Timestamp {timestamp} not found in frame")
        return int(matches[-1])

    def _draw_rectangle(
        self,
        *,
        axis: plt.Axes,
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
        label: str | None = None,
    ) -> None:
        draw_start = max(start_idx, visible_start_idx)
        draw_end = min(end_idx, visible_end_idx)
        if draw_end < draw_start:
            return
        axis.add_patch(
            Rectangle(
                (draw_start - index_shift - 0.5, low),
                (draw_end - draw_start) + 1,
                high - low,
                linewidth=1.2,
                edgecolor=edge_color,
                facecolor=face_color,
                alpha=alpha,
                label=label,
                zorder=1,
            )
        )

    def _draw_trade_band(
        self,
        *,
        axis: plt.Axes,
        start_idx: int,
        end_idx: int,
        visible_start_idx: int,
        visible_end_idx: int,
        low: float,
        high: float,
        index_shift: int,
        face_color: str,
        alpha: float,
    ) -> None:
        draw_start = max(start_idx, visible_start_idx)
        draw_end = min(end_idx, visible_end_idx)
        if draw_end < draw_start:
            return
        axis.add_patch(
            Rectangle(
                (draw_start - index_shift - 0.5, low),
                (draw_end - draw_start) + 1,
                max(high - low, 1e-12),
                linewidth=0.0,
                edgecolor="none",
                facecolor=face_color,
                alpha=alpha,
                zorder=1.5,
            )
        )

    @staticmethod
    def _draw_marker(axis: plt.Axes, x: int, y: float, color: str) -> None:
        axis.scatter(x, y, color=color, s=42, marker="o", zorder=7)

    @staticmethod
    def _draw_vertical_event(
        axis: plt.Axes,
        x: int,
        color: str,
        *,
        alpha: float,
        linewidth: float,
    ) -> None:
        axis.axvline(x, color=color, alpha=alpha, linewidth=linewidth, zorder=6)

    @staticmethod
    def _build_tick_positions(window: pd.DataFrame) -> list[int]:
        timestamps = pd.to_datetime(window["timestamp"].astype("int64"), unit="ms", utc=True)
        hour_positions = [idx for idx, value in enumerate(timestamps) if value.minute == 0]
        if len(hour_positions) >= 3:
            return hour_positions
        half_hour_positions = [idx for idx, value in enumerate(timestamps) if value.minute in {0, 30}]
        if len(half_hour_positions) >= 3:
            return half_hour_positions
        step = max(len(window) // 6, 1)
        return list(range(0, len(window), step))

    @staticmethod
    def _build_tick_labels(window: pd.DataFrame, positions: list[int]) -> list[str]:
        timestamps = pd.to_datetime(window["timestamp"].astype("int64"), unit="ms", utc=True)
        return [timestamps.iloc[position].strftime("%m-%d %H:%M") for position in positions]
