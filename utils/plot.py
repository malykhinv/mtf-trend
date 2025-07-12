import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from mplfinance.original_flavor import candlestick_ohlc
from datetime import datetime
from typing import List

from domain.models.bar import Bar
from domain.models.trendline import Trendline
from utils.logger import log

from matplotlib.dates import AutoDateLocator, DateFormatter

from config.constants import (
    EMA_PERIODS,
    EMA_COLORS,
    CANDLE_WIDTH_MULTIPLIER,
    X_AXIS_MIN_TICKS,
    X_AXIS_MAX_TICKS,
    X_AXIS_MINUTELY_INTERVALS,
    X_AXIS_TIME_FORMAT
)

class Plot:
    def __init__(self, symbol: str, bars: List[Bar]):
        self.symbol = symbol
        self.bars = bars
        self.fig, (self.ax_price, self.ax_vol) = plt.subplots(
            2, 1,
            figsize=(14, 9),
            gridspec_kw={"height_ratios": [3, 1]},
            sharex=True,
            facecolor='#0e1117'
        )
        self.fig.patch.set_facecolor('#0e1117')

        self.ax_price.set_facecolor('#0e1117')
        self.ax_vol.set_facecolor('#0e1117')
        self.ax_price.tick_params(colors='gray', which='both', length=0)
        self.ax_vol.tick_params(colors='gray', which='both', length=0)
        self.ax_price.grid(False)
        self.ax_vol.grid(False)
        self.ax_price.set_title(symbol, color='white', fontsize=14)

    def plot_main(self):
        ohlc = []
        closes = []
        volumes = []

        for bar in self.bars:
            time_num = mdates.date2num(bar.timestamp)
            ohlc.append([time_num, bar.open, bar.high, bar.low, bar.close])
            closes.append(bar.close)
            volumes.append((time_num, bar.volume, bar.close >= bar.open))

        # Динамический width для свечей
        time_nums = [mdates.date2num(bar.timestamp) for bar in self.bars]
        if len(time_nums) >= 2:
            avg_diff = float(np.mean(np.diff(time_nums)))
            width = avg_diff * CANDLE_WIDTH_MULTIPLIER
        else:
            width = 0.0007

        candlestick_ohlc(self.ax_price, ohlc, width=width, colorup='#26a69a', colordown='#ef5350')

        for t, vol, is_up in volumes:
            color = '#26a69a' if is_up else '#ef5350'
            self.ax_vol.bar(t, vol, color=color, width=width)

        # EMA
        closes_array = np.array(closes)
        for period in EMA_PERIODS:
            if len(closes_array) >= period:
                ema = self.ema(closes_array, period)
                times = [mdates.date2num(bar.timestamp) for bar in self.bars]
                self.ax_price.plot(times, ema, linewidth=1.5, color=EMA_COLORS[period], alpha=0.5, label=f'EMA {period}')

        self.ax_price.legend(loc='upper left', fontsize=8, facecolor='#0e1117', labelcolor='white')

        # Ось X
        locator = AutoDateLocator(minticks=X_AXIS_MIN_TICKS, maxticks=X_AXIS_MAX_TICKS)
        locator.intervald[mdates.MINUTELY] = X_AXIS_MINUTELY_INTERVALS
        formatter = DateFormatter(X_AXIS_TIME_FORMAT)

        self.ax_vol.xaxis.set_major_locator(locator)
        self.ax_price.xaxis.set_major_locator(locator)
        self.ax_vol.xaxis.set_major_formatter(formatter)
        self.ax_price.xaxis.set_major_formatter(formatter)

        self.ax_price.tick_params(axis='x', colors='gray', labelsize=8)
        self.ax_vol.tick_params(axis='x', colors='gray', labelsize=8)

        self.fig.tight_layout()

    @staticmethod
    def ema(data, period):
        ema = np.zeros_like(data)
        k = 2 / (period + 1)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = data[i] * k + ema[i - 1] * (1 - k)
        return ema

    def mark_pump_start(self, pump_start_time: datetime):
        pump_start_num = mdates.date2num(pump_start_time)
        self.ax_price.axvline(pump_start_num, color='yellow', linestyle=':', linewidth=1)
        ymax = max(bar.high for bar in self.bars)
        self.ax_price.text(pump_start_num, ymax, 'Start', color='yellow', fontsize=8)
        log("Отмечена точка старта пампа")

    def mark_breakout(self, breakout_idx: int):
        if breakout_idx >= len(self.bars):
            log(f"Индекс пробоя {breakout_idx} выходит за пределы — отметка не будет добавлена")
            return

        time_num = mdates.date2num(self.bars[breakout_idx].timestamp)
        price = self.bars[breakout_idx].close
        self.ax_price.scatter(time_num, price, color='red', s=50, zorder=5, label='Breakout')
        log("Отмечен пробой наклонки")

    def draw_trendline(self, trendline: Trendline, start_idx: int, end_idx: int):
        if not trendline or not trendline.valid:
            log("Невалидная наклонка, не будет нарисована")
            return

        x_indices = list(range(start_idx, end_idx + 1))
        y_values = [trendline.get_value_at(i) for i in x_indices]
        times = [mdates.date2num(self.bars[i].timestamp) for i in x_indices]
        self.ax_price.plot(times, y_values, color='blue', linestyle='-', linewidth=1)
        log("Нарисована наклонка")

    def save(self, filename: str):
        save_dir = ".generated/plot/charts"
        import os
        os.makedirs(save_dir, exist_ok=True)
        full_path = os.path.join(save_dir, filename)
        plt.savefig(full_path, facecolor=self.fig.get_facecolor(), bbox_inches='tight')
        plt.close(self.fig)
