from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import warnings

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle


@dataclass(slots=True)
class StaticComboTradePlotSpec:
    symbol: str
    combo_variant: str
    component_ids: str
    trigger_timestamp_ms: int
    entry_timestamp_ms: int
    exit_timestamp_ms: int | None
    entry_price: float | None
    stop_price: float | None
    exit_price: float | None
    trigger_open: float | None = None
    trigger_high: float | None = None
    trigger_low: float | None = None
    trigger_close: float | None = None
    trigger_return_pct: float | None = None
    trigger_range_pct: float | None = None
    range_atr: float | None = None
    body_atr: float | None = None
    volume_mult: float | None = None
    close_to_high_frac: float | None = None
    pre_base_range_pct_60m: float | None = None
    pre_base_drift_pct_60m: float | None = None
    pre_base_range_vs_trigger: float | None = None
    pre_entry_pullback_frac: float | None = None
    pre_entry_red_volume_frac: float | None = None
    next_bar_pullback_frac: float | None = None
    next_close_to_high_frac: float | None = None
    initial_risk_pct: float | None = None
    peak_timestamp_ms: int | None = None
    peak_price: float | None = None
    exit_return_pct: float | None = None
    exit_reason: str | None = None
    entry_reason: str | None = None
    initial_stop_reason: str | None = None
    source_trade_model_id: str | None = None
    source_trade_model_label: str | None = None
    source_config_id: str | None = None
    hour_utc: int | None = None


class StaticComboTradePlotter:
    _CANDLE_WIDTH = 0.65
    _PRE_TRIGGER_CONTEXT_BARS = 12
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
    _TRIGGER_COLOR = "#a3e635"
    _EXIT_COLOR = "#fde047"
    _RISK_FACE = "#7f1d1d"
    _TP1_FACE = "#14532d"
    _TP2_FACE = "#166534"
    _TP3_FACE = "#15803d"
    _TRIGGER_RANGE_EDGE = "#22d3ee"
    _TRIGGER_RANGE_FACE = "#0f766e"
    _FIGURE_SIZE = (8, 6)
    _FIGURE_DPI = 100
    _GRIDSPEC_HEIGHT_RATIOS = [4, 1]
    _MAX_X_TICKS = 8

    def plot_trade(
        self,
        *,
        frame: pd.DataFrame,
        spec: StaticComboTradePlotSpec,
        output_path: Path,
        include_volume: bool = True,
        dpi: int = 100,
    ) -> None:
        required_columns = {"timestamp", "open", "high", "low", "close", "volume"}
        if frame.empty or not required_columns.issubset(frame.columns):
            raise ValueError("Для графика сделки нужны колонки timestamp/open/high/low/close/volume.")

        prepared = frame.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]].copy()
        for column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = (
            prepared.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
            .sort_values("timestamp")
            .drop_duplicates(subset=["timestamp"], keep="last")
            .reset_index(drop=True)
        )
        if prepared.empty:
            raise ValueError("После нормализации данных окно графика оказалось пустым.")

        trigger_idx = self._timestamp_to_index(prepared, spec.trigger_timestamp_ms)
        entry_idx = self._timestamp_to_index(prepared, spec.entry_timestamp_ms)
        end_anchor = spec.exit_timestamp_ms or spec.peak_timestamp_ms or spec.entry_timestamp_ms
        end_idx = self._timestamp_to_index(prepared, int(end_anchor))
        window_start = max(0, trigger_idx - self._PRE_TRIGGER_CONTEXT_BARS)
        window_end = min(len(prepared) - 1, end_idx)
        window = prepared.iloc[window_start : window_end + 1].reset_index(drop=True)
        x_positions = list(range(len(window)))

        figure, (price_axis, volume_axis) = plt.subplots(
            2,
            1,
            figsize=self._FIGURE_SIZE,
            sharex=True,
            gridspec_kw={"height_ratios": self._GRIDSPEC_HEIGHT_RATIOS},
            facecolor=self._FIGURE_FACE,
        )
        price_axis.set_facecolor(self._AXIS_FACE)
        volume_axis.set_facecolor(self._AXIS_FACE)
        for axis in (price_axis, volume_axis):
            axis.tick_params(colors=self._TEXT_COLOR)
            for spine in axis.spines.values():
                spine.set_color(self._GRID_COLOR)

        self._draw_candles(price_axis, window, x_positions)
        if include_volume:
            self._draw_volume(volume_axis, window, x_positions)
        else:
            volume_axis.set_visible(False)

        trigger_high = spec.trigger_high
        if trigger_high is not None:
            price_axis.axhline(trigger_high, color=self._TRIGGER_RANGE_EDGE, linestyle=":", linewidth=1.0, alpha=0.85)

        tp1_price, _, _ = self._resolve_reward_targets(spec=spec)
        trade_end_idx = self._timestamp_to_index(prepared, spec.exit_timestamp_ms) if spec.exit_timestamp_ms is not None else window_end
        if spec.entry_price is not None and spec.stop_price is not None and spec.entry_price > spec.stop_price:
            self._draw_trade_band(
                axis=price_axis,
                start_idx=entry_idx,
                end_idx=trade_end_idx,
                visible_start_idx=window_start,
                visible_end_idx=window_end,
                low=spec.stop_price,
                high=spec.entry_price,
                index_shift=window_start,
                face_color=self._RISK_FACE,
                alpha=0.24,
            )
        if spec.entry_price is not None and tp1_price is not None and tp1_price > spec.entry_price:
            self._draw_trade_band(
                axis=price_axis,
                start_idx=entry_idx,
                end_idx=trade_end_idx,
                visible_start_idx=window_start,
                visible_end_idx=window_end,
                low=spec.entry_price,
                high=tp1_price,
                index_shift=window_start,
                face_color=self._TP1_FACE,
                alpha=0.18,
            )

        if spec.entry_price is not None:
            price_axis.axhline(spec.entry_price, color=self._ENTRY_COLOR, linestyle="-", linewidth=1.0, alpha=0.95)

        title_parts = [spec.symbol, spec.combo_variant]
        if spec.source_trade_model_id:
            title_parts.append(str(spec.source_trade_model_id))
        if spec.hour_utc is not None:
            title_parts.append(f"{int(spec.hour_utc):02d} UTC")
        price_axis.set_title(" | ".join(title_parts))
        price_axis.title.set_color(self._TEXT_COLOR)
        price_axis.set_ylabel("Цена", color=self._TEXT_COLOR)
        price_axis.grid(alpha=0.18, color=self._GRID_COLOR)

        info_lines = self._build_info_lines(spec=spec, tp1_price=tp1_price)
        price_axis.text(
            0.015,
            0.985,
            "\n".join(info_lines),
            transform=price_axis.transAxes,
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
            volume_axis.set_ylabel("Объём", color=self._TEXT_COLOR)
            volume_axis.grid(alpha=0.18, color=self._GRID_COLOR)
            volume_axis.set_xticks(tick_positions)
            volume_axis.set_xticklabels(tick_labels, rotation=0, ha="center", color=self._TEXT_COLOR)
        else:
            price_axis.set_xticks(tick_positions)
            price_axis.set_xticklabels(tick_labels, rotation=0, ha="center", color=self._TEXT_COLOR)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        figure.subplots_adjust(left=0.07, right=0.985, top=0.93, bottom=0.08, hspace=0.06)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r"Glyph .* missing from font\(s\) DejaVu Sans\.", category=UserWarning)
            figure.savefig(output_path, dpi=max(int(dpi), self._FIGURE_DPI))
        plt.close(figure)

    @staticmethod
    def _resolve_reward_targets(spec: StaticComboTradePlotSpec) -> tuple[float | None, float | None, float | None]:
        if spec.entry_price is None or spec.stop_price is None:
            return None, None, None
        risk = spec.entry_price - spec.stop_price
        if risk <= 0:
            return None, None, None
        tp1_price = spec.entry_price + risk
        tp2_price = spec.entry_price + (risk * 2.0)
        tp3_price = spec.entry_price + (risk * 3.0)
        return tp1_price, tp2_price, tp3_price

    @staticmethod
    def _format_pct(value: float | None) -> str:
        if value is None:
            return "н/д"
        return f"{value * 100:.2f}%"

    @staticmethod
    def _format_ratio(value: float | None) -> str:
        if value is None:
            return "н/д"
        return f"{value:.2f}"

    @classmethod
    def _build_info_lines(
        cls,
        *,
        spec: StaticComboTradePlotSpec,
        tp1_price: float | None,
    ) -> list[str]:
        model_marker = spec.source_trade_model_label or spec.source_trade_model_id or spec.source_config_id or "н/д"
        lines = [
            f"Результат: {cls._format_pct(spec.exit_return_pct)}",
            f"Модель: {model_marker}",
            (
                f"Вход: {spec.entry_price:.5f} | TP: {tp1_price:.5f} | SL: {spec.stop_price:.5f}"
                if spec.entry_price is not None and spec.stop_price is not None and tp1_price is not None
                else (
                    f"Вход: {spec.entry_price:.5f} | Стоп: {spec.stop_price:.5f}"
                    if spec.entry_price is not None and spec.stop_price is not None
                    else "Вход/стоп: н/д"
                )
            ),
            (
                f"Импульс: {cls._format_pct(spec.trigger_return_pct)} | "
                f"Объём: {cls._format_ratio(spec.volume_mult)}x | "
                f"Выход: {spec.exit_reason or 'н/д'}"
            ),
        ]
        if spec.trigger_high is not None:
            lines.append(f"Триггер: {spec.trigger_high:.5f}")
        return lines

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
        timestamp_series = frame["timestamp"].astype("int64")
        matches = frame.index[timestamp_series == int(timestamp)]
        if len(matches) > 0:
            return int(matches[-1])
        insertion_idx = int(timestamp_series.searchsorted(int(timestamp), side="right") - 1)
        if insertion_idx < 0:
            insertion_idx = 0
        if insertion_idx >= len(frame):
            insertion_idx = len(frame) - 1
        return insertion_idx

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
                linewidth=1.2,
                edgecolor=edge_color,
                facecolor=face_color,
                alpha=alpha,
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
    def _thin_tick_positions(positions: list[int], *, max_ticks: int) -> list[int]:
        if len(positions) <= max_ticks:
            return positions
        step = max(int(math.ceil(len(positions) / max_ticks)), 1)
        thinned = positions[::step]
        if positions[-1] not in thinned:
            thinned.append(positions[-1])
        return thinned

    @classmethod
    def _build_tick_positions(cls, window: pd.DataFrame) -> list[int]:
        timestamps = pd.to_datetime(window["timestamp"].astype("int64"), unit="ms", utc=True)
        hour_positions = [idx for idx, value in enumerate(timestamps) if value.minute == 0]
        if len(hour_positions) >= 3:
            return cls._thin_tick_positions(hour_positions, max_ticks=cls._MAX_X_TICKS)
        half_hour_positions = [idx for idx, value in enumerate(timestamps) if value.minute in {0, 30}]
        if len(half_hour_positions) >= 3:
            return cls._thin_tick_positions(half_hour_positions, max_ticks=cls._MAX_X_TICKS)
        step = max(len(window) // 6, 1)
        raw_positions = list(range(0, len(window), step))
        if raw_positions and raw_positions[-1] != len(window) - 1:
            raw_positions.append(len(window) - 1)
        return cls._thin_tick_positions(raw_positions, max_ticks=cls._MAX_X_TICKS)

    @staticmethod
    def _build_tick_labels(window: pd.DataFrame, positions: list[int]) -> list[str]:
        timestamps = pd.to_datetime(window["timestamp"].astype("int64"), unit="ms", utc=True)
        labels: list[str] = []
        for position in positions:
            timestamp = timestamps.iloc[position]
            if timestamp.hour == 0 and timestamp.minute == 0:
                labels.append(timestamp.strftime("%m-%d"))
            else:
                labels.append(timestamp.strftime("%H:%M"))
        return labels
