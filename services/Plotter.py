import os
from typing import Sequence

import matplotlib
matplotlib.use('Agg')  # для headless-режима
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter
import numpy as np

from domain.models.Bar import Bar
from domain.models.Extremum import Extremum
from domain.models.ExtremumType import ExtremumType
from config.constants import (
    COLOR_UP, COLOR_DOWN,
    COLOR_BACKGROUND,
    SWING_COLOR_HIGH,
    SWING_COLOR_LOW,
    SWING_MARKER_SIZE,
    CANDLESTICK_WIDTH_MULTIPLIER,
    TIMEZONE, OUTPUT_PLOT_PATH
)

class Plotter:

    def plot(self, bars: Sequence[Bar], extremums: Sequence[Extremum], path: str = OUTPUT_PLOT_PATH) -> None:
        if not bars:
            raise ValueError("Нет данных для построения")

        fig, ax = plt.subplots(figsize=(14, 6), facecolor=COLOR_BACKGROUND)
        ax.set_facecolor(COLOR_BACKGROUND)
        ax.grid(True, linestyle=':', color='gray', alpha=0.3)

        times = [mdates.date2num(bar.time.astimezone(TIMEZONE)) for bar in bars]
        width = float(np.mean(np.diff(times))) * CANDLESTICK_WIDTH_MULTIPLIER if len(times) > 1 else 0.0007

        for bar, t in zip(bars, times):
            color = COLOR_UP if bar.close >= bar.open else COLOR_DOWN
            ax.plot([t, t], [bar.low, bar.high], color=color, linewidth=1)
            ax.add_patch(plt.Rectangle(
                (t - width / 2, min(bar.open, bar.close)),
                width, abs(bar.close - bar.open),
                color=color
            ))

        # Отображение экстремумов
        ylim = ax.get_ylim()
        y_range = ylim[1] - ylim[0]
        pixel_height = ax.get_window_extent().height or 800
        marker_size_pts = SWING_MARKER_SIZE ** 0.5
        marker_height_data = y_range * (marker_size_pts / pixel_height)

        for ext in extremums:
            t = mdates.date2num(ext.bar.time.astimezone(TIMEZONE))
            price = ext.bar.high if ext.type == ExtremumType.HIGH else ext.bar.low
            marker_y = price + marker_height_data / 2 if ext.type == ExtremumType.HIGH else price - marker_height_data / 2
            color = SWING_COLOR_HIGH if ext.type == ExtremumType.HIGH else SWING_COLOR_LOW
            marker = 'v' if ext.type == ExtremumType.HIGH else '^'
            ax.scatter(t, marker_y, color=color, marker=marker, s=SWING_MARKER_SIZE)

        ax.set_xlim(times[0] - width, times[-1] + width)
        ax.tick_params(colors='gray', labelsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d-%m %H:%M', tz=TIMEZONE))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'{y:.2f}'))

        fig.autofmt_xdate()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        plt.savefig(path, facecolor=fig.get_facecolor(), bbox_inches='tight')
        plt.close(fig)
