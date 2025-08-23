# services/Plotter.py
from __future__ import annotations

import os
import random
import re
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
import matplotlib.ticker as mticker
import numpy as np

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

from domain.models.Bar import Bar
from domain.models.Extremum import Extremum
from domain.models.ExtremumType import ExtremumType


class Plotter:
    """
    Рисует:
      - свечи по всем барам;
      - экстремумы (одной эпохи или множества эпох);
      - горизонтальные линии от каждого экстремума до правого пробоя;
      - ATR снизу, нормализованный в 0–100%;
      - сохраняет график в PNG.
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

        # --- Фигура/оси: 2 строки, отношение высот 3:1 ---
        fig = plt.figure(figsize=(PLOT_WIDTH_INCHES, PLOT_HEIGHT_INCHES),
            facecolor=COLOR_BACKGROUND,
            dpi=PLOT_DPI,
            constrained_layout=False)
        gs = fig.add_gridspec(nrows=2, ncols=1, height_ratios=[3, 1], hspace=0.08)
        ax = fig.add_subplot(gs[0])                    # верх — свечи + экстремумы
        ax_atr = fig.add_subplot(gs[1], sharex=ax)     # низ — ATR
        self._style_axis_base(fig, ax)
        self._style_axis_base(fig, ax_atr)

        # --- Время / ширина свечи ---
        times = self._bars_to_times(bars)
        width = self._calc_candle_width(times)

        # --- Свечи ---
        self._draw_candles(ax, bars, times, width)

        # --- ATR subplot (normalized 0..100) ---
        atr_vals = []
        last = None
        for b in bars:
            v = (b.atr if getattr(b, 'atr', None) is not None else last)
            if v is None:
                v = 0.0
            atr_vals.append(float(v))
            last = v
        if any(val != 0.0 for val in atr_vals):
            vmin = min(atr_vals)
            vmax = max(atr_vals)
            denom = max(vmax - vmin, 1e-12)
            atr_norm = [((v - vmin) / denom) * 100.0 for v in atr_vals]
        else:
            atr_norm = [0.0] * len(atr_vals)
        ax_atr.plot(times, atr_norm, linewidth=1.0, color=SWING_COLOR_LOW, zorder=2)
        ax_atr.fill_between(times, [0] * len(atr_norm), atr_norm, color=SWING_COLOR_LOW, alpha=0.15, zorder=1)
        ax_atr.set_ylim(0, 100)
        ax_atr.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:.0f}%"))
        ax_atr.tick_params(axis="y", colors="gray", labelsize=8)

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
        line_alpha = 0.25
        line_width = 1.2

        # --- Рисуем эпохи ---
        for e_idx, epoch_exts in enumerate(epochs):
            if not epoch_exts:
                continue

            marker_color = epoch_colors[e_idx]
            marker_alpha = 1.0 if (highlight_epoch_index is None or highlight_epoch_index == e_idx) else 0.35

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
                    s=SWING_MARKER_SIZE,
                    c=marker_color,
                    marker=marker,
                    alpha=marker_alpha,
                    zorder=4,
                    label=f"Epoch {e_idx+1}" if show_legend else None,
                )

        # --- X лимиты ---
        left = float(times[0] - width)
        right = float(times[-1] + width)
        ax.set_xlim(left, right)

        # --- Ось X: подписи только внизу (на ATR), стиль TV ---
        ax.tick_params(axis="x", labelbottom=False)

        locator = mdates.AutoDateLocator(minticks=4, maxticks=12)
        ax_atr.xaxis.set_major_locator(locator)
        ax_atr.xaxis.set_major_formatter(TVDateFormatter(TIMEZONE))

        ax_atr.tick_params(axis="x", colors="gray", labelsize=9)
        for lbl in ax_atr.get_xticklabels():
            lbl.set_rotation(0)
            lbl.set_horizontalalignment("center")

        # --- Ось Y цены ---
        def _fmt_price(y: float, _):
            ay = abs(y)
            if ay >= 100:
                return f"{y:,.2f}"
            if ay >= 1:
                return f"{y:,.4f}"
            return f"{y:,.6f}"
        ax.yaxis.set_major_formatter(FuncFormatter(_fmt_price))
        ax.tick_params(axis="y", colors="gray", labelsize=9)

        # Заголовок
        if symbol and timeframe:
            ax.set_title(f"{symbol} {timeframe.value}", color="white", pad=8)

        if len(times) > 1:
            handles, labels = ax.get_legend_handles_labels()
            if handles and show_legend:
                uniq = {}
                for h, l in zip(handles, labels):
                    if l not in uniq:
                        uniq[l] = h
                ax.legend(uniq.values(), uniq.keys(), loc="upper left", fontsize=8)

            # --- Сохранение ---
            full_path = self._resolve_output_path(path, bars, symbol, timeframe)
            os.makedirs(os.path.dirname(full_path) or OUTPUT_PLOT_PATH, exist_ok=True)
            tmp_path = f"{full_path}.part"
            plt.savefig(tmp_path, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight", dpi=PLOT_DPI)
            os.replace(tmp_path, full_path)
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
        ext_type: ExtremumType,
    ) -> int:
        """
        Ищем первый бар справа, чьё закрытие пересекает уровень экстремума.
        Для HIGH — закрытие выше цены; для LOW — закрытие ниже цены.
        Возвращаем индекс пересекающего бара, либо последний индекс, если пересечения нет.
        """
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

            sym = re.sub(r'[\\/:*?"<>|]+', '-', symbol or "")
            fname = f"{sym}_{timeframe.value}_{last_dt:%Y%m%d_%H%M%S}.png"
            return os.path.join(base_dir, fname)
        return path

class TVDateFormatter(mticker.Formatter):
    """
    Метки X «как в TradingView»:
      - интрадей: HH:MM, при смене даты — 'd mon'
      - дневной масштаб: ... 29  31 jun  3  5 ...
      - месячный и крупнее: 'mon' (у первого — 'mon YYYY')
    """
    def __init__(self, tz):
        self.tz = tz
        self._cache_ticks = None
        self._cache_labels = None
        self.axis = None

    def set_axis(self, axis):
        super().set_axis(axis)
        self._cache_ticks = None
        self._cache_labels = None

    def __call__(self, x, pos=None):
        ax = self.axis.axes
        ticks = ax.get_xticks()

        # Перестраиваем кэш, если тики изменились
        if (self._cache_ticks is None
            or len(ticks) != len(self._cache_ticks)
            or not np.allclose(ticks, self._cache_ticks)):

            self._cache_ticks = np.asarray(ticks, dtype=float)
            dts = [mdates.num2date(t, tz=self.tz) for t in self._cache_ticks]

            # Оценим шаг в днях
            if len(dts) > 1:
                diffs = [(dts[i+1] - dts[i]).total_seconds() / 86400.0
                         for i in range(len(dts)-1)]
                spacing = float(np.median(np.abs(diffs))) if diffs else 1.0
            else:
                spacing = 1.0

            labels = []
            if spacing >= 30.0:
                # Месячный масштаб и крупнее
                prev_year = None
                for dt in dts:
                    mon = dt.strftime("%b").lower()
                    label = mon if dt.year == prev_year else f"{mon} {dt.year}"
                    prev_year = dt.year
                    labels.append(label)
            elif spacing >= 1.0:
                # Дневной масштаб
                prev_month = None
                for dt in dts:
                    if dt.month == prev_month:
                        labels.append(str(dt.day))  # только число дня
                    else:
                        labels.append(dt.strftime("%b").lower())  # метка месяца
                        prev_month = dt.month
            else:
                # Интрадей
                prev_date = None
                for dt in dts:
                    if prev_date is None or dt.date() != prev_date:
                        labels.append(f"{dt.day} {dt.strftime('%b').lower()}")
                        prev_date = dt.date()
                    else:
                        labels.append(dt.strftime("%H:%M"))

            self._cache_labels = labels

        # Ближайший тик — его и подписываем
        idx = int(np.argmin(np.abs(self._cache_ticks - float(x))))
        return self._cache_labels[idx]
