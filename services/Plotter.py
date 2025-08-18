# services/Plotter.py
import os
import random
from itertools import chain, tee
from typing import Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter
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
        highlight_epoch_index: int | None = None,  # можно подсветить конкретную эпоху (например, ту, где найден разворот)
        show_legend: bool = False,
    ) -> None:
        if not bars:
            raise ValueError("Нет данных для построения")

        # --- Нормализуем вход: превращаем в список эпох ---
        epochs: list[list[Extremum]] = self._normalize_epochs(extremums)

        # --- Фигура/оси ---
        fig, ax = plt.subplots(figsize=(14, 6), facecolor=COLOR_BACKGROUND)
        ax.set_facecolor(COLOR_BACKGROUND)
        ax.grid(True, linestyle=":", color="gray", alpha=0.3)

        # --- Время / ширина свечи ---
        times = self._bars_to_times(bars)
        width = self._calc_candle_width(times)

        # --- Свечи ---
        self._draw_candles(ax, bars, times, width)

        # --- Подготовка геометрии для маркеров ---
        ylim = ax.get_ylim()
        y_range = ylim[1] - ylim[0]
        pixel_height = ax.get_window_extent().height or 800
        marker_size_pts = SWING_MARKER_SIZE ** 0.5
        marker_height_data = y_range * (marker_size_pts / pixel_height)

        # --- Палитра эпох (для маркеров) ---
        epoch_colors = self._epoch_palette(len(epochs)) if len(epochs) > 1 else [SWING_COLOR_HIGH]

        # --- Цвета горизонтальных линий (High/Low общие для всех эпох) ---
        line_color_high = SWING_COLOR_HIGH
        line_color_low = SWING_COLOR_LOW
        line_alpha = 0.55
        line_width = 1.2

        # --- Рисуем эпохи ---
        for e_idx, epoch_exts in enumerate(epochs):
            if not epoch_exts:
                continue

            # Цвет маркеров для эпохи
            marker_color = epoch_colors[e_idx]
            marker_alpha = 1.0 if (highlight_epoch_index is None or highlight_epoch_index == e_idx) else 0.35
            marker_size = SWING_MARKER_SIZE if marker_alpha == 1.0 else max(10, int(SWING_MARKER_SIZE * 0.7))

            # Маркеры + горизонтальные линии
            for ext in epoch_exts:
                bar = ext.bar
                price = bar.high if ext.type == ExtremumType.HIGH else bar.low
                # индекс бара экстремума
                try:
                    start_idx = bars.index(bar)
                except ValueError:
                    # Если пришёл другой объект Bar (не тот же инстанс), ищем по времени/цене
                    start_idx = self._find_bar_index_heuristic(bars, bar)

                # --- горизонтальная линия до пересечения/конца ---
                end_idx = self._find_right_intersection_index(bars, start_idx, price)
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

                # --- маркер экстремума ---
                t = times[start_idx]
                marker_y = (
                    price + marker_height_data / 2
                    if ext.type == ExtremumType.HIGH
                    else price - marker_height_data / 2
                )
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

        # --- Оформление осей ---
        left = float(times[0] - width)
        right = float(times[-1] + width)
        ax.set_xlim(left, right)
        ax.tick_params(colors="gray", labelsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%m %H:%M", tz=TIMEZONE))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:.6f}"))

        if show_legend and len(epochs) > 1:
            # Легенда по эпохам (если много эпох). Дубликаты лейблов уберём.
            handles, labels = ax.get_legend_handles_labels()
            uniq = {}
            for h, l in zip(handles, labels):
                uniq.setdefault(l, h)
            ax.legend(uniq.values(), uniq.keys(), loc="upper left", fontsize=8)

        fig.autofmt_xdate()

        # --- Сохранение ---
        os.makedirs(os.path.dirname(path) or OUTPUT_PLOT_PATH, exist_ok=True)
        plt.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)

    # ----------------- ВСПОМОГАТЕЛЬНЫЕ -----------------

    @staticmethod
    def _normalize_epochs(extremums) -> list[list[Extremum]]:
        # Пусто / None
        if extremums is None:
            return []

        # Поддержка генераторов: делаем «peek» без потери данных
        try:
            it1, _ = tee(extremums)
        except TypeError:
            return []

        try:
            first = next(it1)
        except StopIteration:
            return []

        # Случай 1: пришёл плоский список экстремумов -> одна эпоха
        if isinstance(first, Extremum):
            rest = list(it1)
            return [[first, *rest]]

        # Случай 2: пришёл список эпох (каждая эпоха — Iterable экстремумов)
        epochs: list[list[Extremum]] = []
        for maybe_epoch in chain([first], it1):
            if isinstance(maybe_epoch, Extremum):
                # На случай странного входа: элемент — одиночный экстремум, а не последовательность
                epochs.append([maybe_epoch])
            else:
                epochs.append(list(maybe_epoch))
        return epochs

    @staticmethod
    def _find_bar_index_heuristic(bars: Sequence[Bar], sample: Bar) -> int:
        """
        На случай, когда Extremum.bar не тот же экземпляр, что в массиве bars:
        ищем по времени (приоритет), затем по (open, high, low, close).
        """
        for i, b in enumerate(bars):
            if b.time == sample.time:
                return i
        for i, b in enumerate(bars):
            if (b.open, b.high, b.low, b.close) == (sample.open, sample.high, sample.low, sample.close):
                return i
        # fallback — лучше вернуть самый правый индекс, чтобы линия точно попала в диапазон
        return len(bars) - 1

    @staticmethod
    def _bars_to_times(bars: Sequence[Bar]) -> np.ndarray:
        """Вектор времени в формате matplotlib date."""
        vals = [float(mdates.date2num(bar.time.astimezone(TIMEZONE))) for bar in bars]
        return np.asarray(vals, dtype=np.float64).reshape(-1, )

    @staticmethod
    def _calc_candle_width(times: np.ndarray) -> float:
        """Ширина свечи как доля среднего шага времени."""
        if times.size <= 1:
            return 0.0007
        return float(np.mean(np.diff(times))) * float(CANDLESTICK_WIDTH_MULTIPLIER)

    @staticmethod
    def _draw_candles(ax: plt.Axes, bars: Sequence[Bar], times: np.ndarray, width: float) -> None:
        """Рисуем свечи с тенями."""
        for bar, t in zip(bars, times):
            color = COLOR_UP if bar.close >= bar.open else COLOR_DOWN
            # тени
            ax.plot([t, t], [bar.low, bar.high], color=color, linewidth=1, zorder=2)
            # тела
            ax.add_patch(
                plt.Rectangle(
                    (t - width / 2, min(bar.open, bar.close)),
                    width,
                    abs(bar.close - bar.open),
                    color=color,
                    zorder=2,
                )
            )

    @staticmethod
    def _find_right_intersection_index(bars: Sequence[Bar], start_idx: int, price: float) -> int:
        """
        Ищем индекс первой свечи справа от start_idx, которая «пересекает» горизонтальный уровень.
        Пересечение трактуем как попадание уровня в диапазон [low, high] свечи.
        Если не нашли — тянем линию до последней свечи.
        """
        n = len(bars)
        for i in range(start_idx + 1, n):
            if bars[i].low <= price <= bars[i].high:
                return i
        return n - 1

    @staticmethod
    def _epoch_palette(n: int) -> list[str]:
        """
        Палитра для окраски эпох (маркеры). Случайная выборка из табличной палитры,
        чтобы цвета были приятные / контрастные.
        """
        base = [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
            "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
            "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff8100",
            "#a65628", "#f781bf", "#999999", "#66c2a5", "#fc8d62",
        ]
        random.shuffle(base)
        if n <= len(base):
            return base[:n]
        # если эпох больше — просто повторим с небольшим шумом альфы
        out = []
        for i in range(n):
            out.append(base[i % len(base)])
        return out
