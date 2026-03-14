"""Matplotlib plotter for bee_bite stage-1 review."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from data.liquidity.bee_bite_stage1_selector import BeeBiteStage1Result


class BeeBiteStage1Plotter:
    """Render a compact stage-1 review chart with OHLC candles and annotations."""

    _CANDLE_WIDTH = 0.65
    _PRE_CONTEXT_BARS = 24
    _POST_CONTEXT_BARS = 24
    _FIGURE_FACE = "#08111f"
    _AXIS_FACE = "#0f172a"
    _GRID_COLOR = "#334155"
    _TEXT_COLOR = "#e2e8f0"
    _UP_COLOR = "#22c55e"
    _DOWN_COLOR = "#f97316"
    _HOLD_COLOR = "#f59e0b"
    _PEAK_COLOR = "#fb7185"
    _START_COLOR = "#38bdf8"
    _STAGE1_COLOR = "#a3e635"
    _LOWEST_COLOR = "#c084fc"

    def plot_event(
        self,
        *,
        frame: pd.DataFrame,
        event: BeeBiteStage1Result,
        output_path: Path,
        window_end_timestamp: int | None = None,
        title_suffix: str | None = None,
    ) -> None:
        required_columns = {"timestamp", "open", "high", "low", "close", "volume"}
        if frame.empty or not required_columns.issubset(frame.columns):
            raise ValueError("Stage-1 plot requires timestamp/open/high/low/close/volume columns.")

        prepared = frame.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]].copy()
        for column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
        prepared = prepared.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
        if prepared.empty:
            raise ValueError("Stage-1 plot received an empty frame after normalization.")

        confirmed_idx = self._timestamp_to_index(prepared, event.stage1_confirmed_timestamp)
        pump_start_idx = self._timestamp_to_index(prepared, event.pump_start_timestamp)
        pump_peak_idx = self._timestamp_to_index(prepared, event.pump_peak_timestamp)
        lowest_after_pump_idx = (
            self._timestamp_to_index(prepared, event.lowest_after_pump_timestamp)
            if event.lowest_after_pump_timestamp is not None
            else None
        )

        explicit_window_end_idx = (
            self._timestamp_to_index(prepared, window_end_timestamp)
            if window_end_timestamp is not None
            else confirmed_idx
        )
        window_start = max(0, pump_start_idx - self._PRE_CONTEXT_BARS)
        window_end = min(len(prepared) - 1, explicit_window_end_idx)
        window = prepared.iloc[window_start : window_end + 1].reset_index(drop=True)
        x_positions = list(range(len(window)))
        index_shift = window_start

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

        if event.hold_price is not None:
            price_ax.axhline(
                event.hold_price,
                color=self._HOLD_COLOR,
                linestyle="--",
                linewidth=1.2,
                alpha=0.9,
                label="0.5 hold",
            )
        if event.pump_peak_price is not None:
            price_ax.axhline(
                event.pump_peak_price,
                color=self._PEAK_COLOR,
                linestyle=":",
                linewidth=1.0,
                alpha=0.8,
                label="pump peak",
            )

        self._draw_marker(price_ax, pump_start_idx - index_shift, event.pump_base_price, self._START_COLOR, "pump start")
        self._draw_marker(price_ax, pump_peak_idx - index_shift, event.pump_peak_price, self._PEAK_COLOR, "pump peak marker")
        close_at_confirmed = float(window.iloc[confirmed_idx - index_shift]["close"])
        self._draw_marker(price_ax, confirmed_idx - index_shift, close_at_confirmed, self._STAGE1_COLOR, "stage1 confirmed")

        if event.lowest_after_pump is not None and lowest_after_pump_idx is not None:
            price_ax.scatter(
                lowest_after_pump_idx - index_shift,
                event.lowest_after_pump,
                color=self._LOWEST_COLOR,
                s=50,
                marker="x",
                label="lowest after pump",
                zorder=5,
            )

        stage1_confirmed_text = _format_ts_label(event.stage1_confirmed_timestamp)
        pump_pct = float(event.pump_percent or 0.0) * 100.0
        retrace_pct = max(0.0, (1.0 - float(event.retain_ratio or 0.0)) * 100.0)
        volume_ratio = float(event.post_pump_volume_ratio or 0.0)
        title = f"{event.symbol} | stage1 confirmed {stage1_confirmed_text}"
        if title_suffix:
            title = f"{title} | {title_suffix}"
        price_ax.set_title(title)
        price_ax.title.set_color(self._TEXT_COLOR)
        price_ax.set_ylabel("Price", color=self._TEXT_COLOR)
        price_ax.grid(alpha=0.18, color=self._GRID_COLOR)
        price_ax.text(
            0.015,
            0.985,
            f"Рост: {pump_pct:.2f}%\nОткат: {retrace_pct:.2f}%\nРост объема: {volume_ratio:.2f}x",
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

    def _draw_candles(self, axis, frame: pd.DataFrame, x_positions: list[int]) -> None:
        for idx, row in zip(x_positions, frame.itertuples(index=False), strict=False):
            open_price = float(row.open)
            high_price = float(row.high)
            low_price = float(row.low)
            close_price = float(row.close)
            color = self._UP_COLOR if close_price >= open_price else self._DOWN_COLOR
            axis.vlines(idx, low_price, high_price, color=color, linewidth=1.0, alpha=0.9)
            body_low = min(open_price, close_price)
            body_height = abs(close_price - open_price)
            if body_height == 0:
                axis.hlines(body_low, idx - self._CANDLE_WIDTH / 2, idx + self._CANDLE_WIDTH / 2, color=color, linewidth=1.2)
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
                )
            )

    def _draw_volume(self, axis, frame: pd.DataFrame, x_positions: list[int]) -> None:
        colors = [self._UP_COLOR if row.close >= row.open else self._DOWN_COLOR for row in frame.itertuples(index=False)]
        axis.bar(x_positions, frame["volume"], color=colors, width=self._CANDLE_WIDTH, alpha=0.85)

    @staticmethod
    def _draw_marker(axis, x_idx: int, price: float | None, color: str, label: str) -> None:
        if price is None:
            return
        axis.scatter(x_idx, price, color=color, s=55, zorder=6, label=label)
        axis.axvline(x_idx, color=color, linewidth=0.9, alpha=0.35)

    @staticmethod
    def _timestamp_to_index(frame: pd.DataFrame, timestamp: int | None) -> int:
        if timestamp is None:
            raise ValueError("Stage-1 plot requires event timestamps.")
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


def _format_ts_label(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "n/a"
    return pd.to_datetime(timestamp_ms, unit="ms", utc=True).strftime("%m-%d %H:%M UTC")
