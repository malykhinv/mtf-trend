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

    def plot_event(
        self,
        *,
        frame: pd.DataFrame,
        event: BeeBiteStage1Result,
        output_path: Path,
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

        sleep_start_idx = self._timestamp_to_index(prepared, event.sleep_start_timestamp)
        sleep_end_idx = self._timestamp_to_index(prepared, event.sleep_end_timestamp)
        confirmed_idx = self._timestamp_to_index(prepared, event.stage1_confirmed_timestamp)
        pump_start_idx = self._timestamp_to_index(prepared, event.pump_start_timestamp)
        pump_peak_idx = self._timestamp_to_index(prepared, event.pump_peak_timestamp)

        window_start = max(0, sleep_start_idx - self._PRE_CONTEXT_BARS)
        window_end = min(len(prepared) - 1, confirmed_idx + self._POST_CONTEXT_BARS)
        window = prepared.iloc[window_start : window_end + 1].reset_index(drop=True)
        x_positions = list(range(len(window)))
        index_shift = window_start

        fig, (price_ax, volume_ax) = plt.subplots(
            2,
            1,
            figsize=(16, 9),
            sharex=True,
            gridspec_kw={"height_ratios": [4, 1]},
        )

        self._draw_candles(price_ax, window, x_positions)
        self._draw_volume(volume_ax, window, x_positions)

        price_ax.axvspan(
            sleep_start_idx - index_shift,
            sleep_end_idx - index_shift,
            color="#d6eaf8",
            alpha=0.35,
            label="sleep",
        )
        if event.hold_price is not None:
            price_ax.axhline(event.hold_price, color="#f39c12", linestyle="--", linewidth=1.2, label="0.5 hold")
        if event.pump_peak_price is not None:
            price_ax.axhline(event.pump_peak_price, color="#c0392b", linestyle=":", linewidth=1.0, label="pump peak")

        self._draw_marker(price_ax, pump_start_idx - index_shift, event.pump_base_price, "pump start", "#1f77b4")
        self._draw_marker(price_ax, pump_peak_idx - index_shift, event.pump_peak_price, "pump peak", "#c0392b")
        close_at_confirmed = float(window.iloc[confirmed_idx - index_shift]["close"])
        self._draw_marker(price_ax, confirmed_idx - index_shift, close_at_confirmed, "stage1", "#27ae60")

        if event.lowest_after_pump is not None:
            price_ax.scatter(
                confirmed_idx - index_shift,
                event.lowest_after_pump,
                color="#8e44ad",
                s=50,
                marker="x",
                label="lowest after pump",
                zorder=5,
            )

        title = (
            f"{event.symbol} | stage1 confirmed={event.stage1_confirmed_timestamp} | "
            f"pump={float(event.pump_percent or 0.0) * 100:.2f}% | "
            f"retain={float(event.retain_ratio or 0.0) * 100:.2f}% | "
            f"vol_ratio={float(event.post_pump_volume_ratio or 0.0):.2f}x"
        )
        price_ax.set_title(title)
        price_ax.set_ylabel("Price")
        price_ax.grid(alpha=0.15)
        price_ax.legend(loc="upper left")

        volume_ax.set_ylabel("Volume")
        volume_ax.grid(alpha=0.15)
        tick_positions = self._build_tick_positions(len(window))
        tick_labels = self._build_tick_labels(window, tick_positions)
        volume_ax.set_xticks(tick_positions)
        volume_ax.set_xticklabels(tick_labels, rotation=45, ha="right")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.tight_layout()
        fig.savefig(output_path, dpi=160)
        plt.close(fig)

    def _draw_candles(self, axis, frame: pd.DataFrame, x_positions: list[int]) -> None:
        for idx, row in zip(x_positions, frame.itertuples(index=False), strict=False):
            color = "#2ecc71" if row.close >= row.open else "#e74c3c"
            axis.vlines(idx, row.low, row.high, color=color, linewidth=1.0, alpha=0.9)
            body_low = min(row.open, row.close)
            body_height = abs(row.close - row.open)
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
        colors = ["#2ecc71" if row.close >= row.open else "#e74c3c" for row in frame.itertuples(index=False)]
        axis.bar(x_positions, frame["volume"], color=colors, width=self._CANDLE_WIDTH, alpha=0.85)

    @staticmethod
    def _draw_marker(axis, x_idx: int, price: float | None, label: str, color: str) -> None:
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
