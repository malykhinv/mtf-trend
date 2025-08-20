# services/Plotter.py
from __future__ import annotations

import os
import random
from itertools import chain, tee
from typing import Sequence

import matplotlib
from domain.models.Timeframe import Timeframe
from utils.logger import logw

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter
from matplotlib.patches import Rectangle
import numpy as np

from domain.models.Bar import Bar
from domain.models.Extremum import Extremum
from domain.models.ExtremumType import ExtremumType

from config.constants import (
    TIMEZONE,
    COLOR_BACKGROUND,
    COLOR_UP,
    COLOR_DOWN,
    SWING_MARKER_SIZE,
    SWING_COLOR_HIGH,
    SWING_COLOR_LOW,
    CANDLESTICK_WIDTH_MULTIPLIER,
    OUTPUT_PLOT_PATH,
    PLOT_WIDTH_INCHES,
    PLOT_HEIGHT_INCHES,
    PLOT_DPI,
)


class Plotter:
    """
    Рисует:
      - свечи по всем барам;
      - экстремумы (одной эпохи или множества эпох);
      - горизонтальные линии от каждого экстремума до пересечения со свечой (или до конца графика).
    Варианты входа:
      1) extremums: Sequence[Extremum] -> считаем, что это одна эпоха (например, когда разворот найден).
      2) extremums: Sequence[Sequence[Extremum]] -> несколько эпох (например, разворот не найден).
    """

    def plot(
        self,
        bars: Sequence[Bar],
        extremums: Sequence[Extremum] | Sequence[Sequence[Extremum]],
        path: str = OUTPUT_PLOT_PATH,
        *,
        symbol: str | None = None,
        timeframe: Timeframe | str | None = None,
        highlight_epoch_index: int | None = None,
        show_legend: bool = False,
    ) -> None:
        if not bars:
            raise ValueError("Нет данных для построения")

        # --- Нормализуем вход: превращаем в список эпох ---
        epochs: list[list[Extremum]] = self._normalize_epochs(extremums)

        # --- Фигура/ось ---
        fig, ax = plt.subplots(
            figsize=(PLOT_WIDTH_INCHES, PLOT_HEIGHT_INCHES),
            facecolor=COLOR_BACKGROUND,
            dpi=PLOT_DPI,
            constrained_layout=True,
        )
        self._style_axis_base(fig, ax)

        # --- Время / ширина свечи ---
        times = self._bars_to_times(bars)
        width = self._calc_candle_width(times)

        # --- Свечи ---
        self._draw_candles(ax, bars, times, width)

        # --- Подготовка геометрии для маркеров ---
        ylim = ax.get_ylim()
        y_range = max(1e-12, ylim[1] - ylim[0])
        pixel_height = ax.get_window_extent().height or 800
        marker_size_pts = SWING_MARKER_SIZE ** 0.5  # так как s — это площадь в pt²
        marker_height_data = y_range * (marker_size_pts / pixel_height)

        # --- Палитра эпох (для маркеров) ---
        epoch_colors = self._epoch_palette(len(epochs)) if len(epochs) > 1 else [SWING_COLOR_HIGH]

        # --- Цвета горизонтальных линий ---
        line_color_high = SWING_COLOR_HIGH
        line_color_low = SWING_COLOR_LOW
        line_alpha = 0.55
        line_width = 1.2

        # --- Рисуем эпохи ---
        for e_idx, epoch_exts in enumerate(epochs):
            if not epoch_exts:
                continue

            marker_color = epoch_colors[e_idx]
            marker_alpha = 1.0 if (highlight_epoch_index is None or highlight_epoch_index == e_idx) else 0.35
            marker_size = SWING_MARKER_SIZE if marker_alpha == 1.0 else max(10, int(SWING_MARKER_SIZE * 0.7))

            for ext in epoch_exts:
                bar = ext.bar
                price = bar.high if ext.type == ExtremumType.HIGH else bar.low
                # индекс бара экстремума
                try:
                    start_idx = bars.index(bar)
                except ValueError:
                    start_idx = self._find_bar_index_heuristic(bars, bar)

                # горизонтальная линия
                end_idx = self._find_right_intersection_index(bars, start_idx, price, ext.type)
                x0 = times[start_idx]
                x1 = times[end_idx]
                ax.hlines(
                    y=price,
                    xmin=x0,
                    xmax=x1,
                    colors=line_color_high if ext.type == ExtremumType.HIGH else line_color_low,
                    linestyles="--",
                    linewidth=line_width,
                    alpha=line_alpha,
                    zorder=3,
                )

                # маркер экстремума
                t = times[start_idx]
                marker_y = price + marker_height_data / 2 if ext.type == ExtremumType.HIGH else price - marker_height_data / 2
                marker = "v" if ext.type == ExtremumType.HIGH else "^"
                ax.scatter(
                    t,
                    marker_y,
                    color=marker_color,
                    alpha=marker_alpha,
                    marker=marker,
                    s=marker_size,
                    zorder=4,
                    label=f"Epoch {e_idx+1}" if show_legend else None,
                )

        # --- Оформление осей, лимитов и заголовка ---
        left = float(times[0] - width)
        right = float(times[-1] + width)
        ax.set_xlim(left, right)

        # X — автолокация + "умные" подписи без поворота (аналог TradingView)
        locator = mdates.AutoDateLocator()
        formatter = mdates.ConciseDateFormatter(locator, tz=TIMEZONE)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)
        ax.tick_params(axis="x", colors="gray", labelsize=9, rotation=0)
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(0)
            lbl.set_horizontalalignment("center")

        # Y — аккуратное форматирование без фиксированных 6 знаков
        def _fmt_price(y: float, _):
            ay = abs(y)
            if ay >= 100:
                return f"{y:,.2f}"
            if ay >= 1:
                return f"{y:,.4f}"
            return f"{y:,.6f}"
        ax.yaxis.set_major_formatter(FuncFormatter(_fmt_price))
        ax.tick_params(axis="y", colors="gray", labelsize=9)

        # Заголовок: "SYMBOL TF"
        if symbol and timeframe:
            ax.set_title(f"{symbol} {timeframe.value}", color="white", pad=8)

            if show_legend and len(epochs) > 1:
                handles, labels = ax.get_legend_handles_labels()
                uniq = {}
                for h, l in zip(handles, labels):
                    if l not in uniq:
                        uniq[l] = h
                ax.legend(uniq.values(), uniq.keys(), loc="upper left", fontsize=8)

            # --- Сохранение ---
            full_path = self._resolve_output_path(path, bars, symbol, timeframe)
            os.makedirs(os.path.dirname(full_path) or OUTPUT_PLOT_PATH, exist_ok=True)
            plt.savefig(full_path, facecolor=fig.get_facecolor(), bbox_inches="tight", dpi=PLOT_DPI)
            plt.close(fig)
        else:
            logw(f"Не хватает данных для построения графика: symbol={symbol}, timeframe={timeframe}")

    @staticmethod
    def _style_axis_base(fig: plt.Figure, ax: plt.Axes) -> None:
        fig.patch.set_facecolor(COLOR_BACKGROUND)
        ax.set_facecolor(COLOR_BACKGROUND)
        ax.grid(True, color="gray", linestyle=":", linewidth=0.5, alpha=0.25)
        ax.tick_params(colors="gray", which="both", length=0)

    @staticmethod
    def _normalize_epochs(extremums) -> list[list[Extremum]]:
        if extremums is None:
            return []
        try:
            it1, _ = tee(extremums)
        except TypeError:
            return []
        try:
            first = next(it1)
        except StopIteration:
            return []
        if isinstance(first, Extremum):
            rest = list(it1)
            return [[first, *rest]]
        epochs: list[list[Extremum]] = []
        for maybe_epoch in chain([first], it1):
            if isinstance(maybe_epoch, Extremum):
                epochs.append([maybe_epoch])
            else:
                epochs.append(list(maybe_epoch))
        return epochs

    @staticmethod
    def _find_bar_index_heuristic(bars: Sequence[Bar], sample: Bar) -> int:
        for i, b in enumerate(bars):
            if b.time == sample.time:
                return i
        for i, b in enumerate(bars):
            if (b.open, b.high, b.low, b.close) == (sample.open, sample.high, sample.low, sample.close):
                return i
        return len(bars) - 1

    @staticmethod
    def _bars_to_times(bars: Sequence[Bar]) -> np.ndarray:
        vals: list[float] = []
        for bar in bars:
            dt = bar.time
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TIMEZONE)
            else:
                dt = dt.astimezone(TIMEZONE)
            vals.append(float(mdates.date2num(dt)))
        return np.asarray(vals, dtype=np.float64).reshape(-1,)

    @staticmethod
    def _calc_candle_width(times: np.ndarray) -> float:
        if times.size <= 1:
            return 0.0007
        return float(np.mean(np.diff(times))) * float(CANDLESTICK_WIDTH_MULTIPLIER)

    @staticmethod
    def _draw_candles(ax: plt.Axes, bars: Sequence[Bar], times: np.ndarray, width: float) -> None:
        for bar, t in zip(bars, times):
            color = COLOR_UP if bar.close >= bar.open else COLOR_DOWN
            ax.plot([t, t], [bar.low, bar.high], color=color, linewidth=1, zorder=2)
            ax.add_patch(
                Rectangle(
                    (t - width / 2, min(bar.open, bar.close)),
                    width,
                    max(1e-12, abs(bar.close - bar.open)),
                    facecolor=color,
                    edgecolor=color,
                    zorder=2,
                )
            )

    @staticmethod
    def _find_right_intersection_index(
            bars: Sequence[Bar],
            start_idx: int,
            price: float,
            ext_type: ExtremumType
    ) -> int:
        n = len(bars)
        for i in range(start_idx + 1, n):
            if ext_type == ExtremumType.LOW:
                if bars[i].close < price:
                    return i
            elif ext_type == ExtremumType.HIGH:
                if bars[i].close > price:
                    return i
        return n - 1

    @staticmethod
    def _epoch_palette(n: int) -> list[str]:
        base = [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
            "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
            "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff8100",
            "#a65628", "#f781bf", "#999999", "#66c2a5", "#fc8d62",
        ]
        random.shuffle(base)
        if n <= len(base):
            return base[:n]
        out: list[str] = []
        for i in range(n):
            out.append(base[i % len(base)])
        return out

    @staticmethod
    def _resolve_output_path(
        path: str,
        bars: Sequence[Bar],
        symbol: str,
        timeframe: Timeframe,
    ) -> str:
        """
        Если передана директория/путь без расширения — сгенерировать имя файла.
        """
        is_dir = os.path.isdir(path)
        has_ext = os.path.splitext(path)[1] != ""
        if is_dir or not has_ext:
            last_dt = bars[-1].time
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=TIMEZONE)
            else:
                last_dt = last_dt.astimezone(TIMEZONE)

            base_dir = path if is_dir else (path or OUTPUT_PLOT_PATH)
            os.makedirs(base_dir, exist_ok=True)

            sym = symbol.replace("/", "")
            fname = f"{sym}_{timeframe.value}_{last_dt:%Y%m%d_%H%M%S}.png"
            return os.path.join(base_dir, fname)
        return path
