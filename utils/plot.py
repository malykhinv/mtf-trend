import asyncio

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from mplfinance.original_flavor import candlestick_ohlc
from datetime import datetime
from typing import List, Optional

from domain.models.bar import Bar
from domain.models.timeframe import Timeframe
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
    X_AXIS_TIME_FORMAT,
    FLOAT_UNDEFINED,
    COLOR_UP,
    COLOR_DOWN,
    COLOR_TRENDLINE,
    COLOR_PUMP_START,
    TRENDLINE_WIDTH,
    TRENDLINE_STYLE,
    PUMP_START_LINE_STYLE,
    PUMP_START_LINE_WIDTH,
    PUMP_START_TEXT_SIZE,
    EMA_ALPHA,
    EMA_LINEWIDTH,
    LEGEND_FONT_SIZE, COLOR_FACE, BELGRADE_TZ,
)


class Plot:
    def __init__(self,
                 symbol: str,
                 bars: List[Bar],
                 tf: Timeframe,
                 message: Optional[str] = None,
                 save_dir=".generated/plot/charts"):
        self.symbol = symbol
        self.bars = bars
        self.save_dir = save_dir
        self.fig, (self.ax_price, self.ax_vol, self.ax_oi) = plt.subplots(
            3, 1,
            figsize=(14, 12),
            gridspec_kw={"height_ratios": [3, 1, 1]},
            sharex=True,
            facecolor=COLOR_FACE
        )
        self.fig.patch.set_facecolor(COLOR_FACE)

        for ax in [self.ax_price, self.ax_vol, self.ax_oi]:
            ax.set_facecolor(COLOR_FACE)
            ax.tick_params(colors='gray', which='both', length=0)
            ax.grid(False)

        self.ax_price.set_title(f"{symbol} ({tf.value})", color='white', fontsize=14)

        if message:
            self.ax_price.text(
                0.01, 0.85, message, transform=self.ax_price.transAxes,
                fontsize=10, color='orange', ha='left', va='bottom'
            )

    async def generate_and_save(self,
                                filename: str, *,
                                pump_start_time: datetime,
                                trendline: Optional[Trendline] = None):
        await asyncio.to_thread(self._generate_and_save_sync, filename, pump_start_time, trendline)

    def _generate_and_save_sync(self, filename: str, pump_start_time, trendline):
        self.plot_main()
        self.mark_pump_start(pump_start_time)
        if trendline:
            self.draw_trendline(trendline)
        self.save(filename)

    def plot_main(self):
        ohlc, closes, volumes, oi_values = [], [], [], []

        for bar in self.bars:
            ts_belgrade = bar.timestamp.astimezone(BELGRADE_TZ)
            time_num = mdates.date2num(ts_belgrade)
            ohlc.append([time_num, bar.open, bar.high, bar.low, bar.close])
            closes.append(bar.close)
            volumes.append((time_num, bar.volume, bar.close >= bar.open))
            oi_values.append(bar.oi)

        # Динамический width для свечей
        time_nums = [mdates.date2num(bar.timestamp.astimezone(BELGRADE_TZ)) for bar in self.bars]
        if len(time_nums) >= 2:
            avg_diff = float(np.mean(np.diff(time_nums)))
            width = avg_diff * CANDLE_WIDTH_MULTIPLIER
        else:
            width = 0.0007

        candlestick_ohlc(self.ax_price, ohlc, width=width, colorup=COLOR_UP, colordown=COLOR_DOWN)

        # Volume normalization
        vol_values = np.array([v[1] for v in volumes])
        vol_min, vol_max = vol_values.min(), vol_values.max()

        for t, vol, is_up in volumes:
            color = COLOR_UP if is_up else COLOR_DOWN
            self.ax_vol.bar(t, vol, color=color, width=width)

        self.ax_vol.set_ylim(vol_min, vol_max * 1.05)

        closes_array = np.array(closes)
        for period in EMA_PERIODS:
            if len(closes_array) >= period:
                ema = self.ema(closes_array, period)
                self.ax_price.plot(time_nums, ema, linewidth=EMA_LINEWIDTH, color=EMA_COLORS[period], alpha=EMA_ALPHA,
                                   label=f'EMA {period}')

        self.ax_price.legend(loc='upper left', fontsize=LEGEND_FONT_SIZE, facecolor=COLOR_FACE, labelcolor='white')

        # OI plot normalization
        has_oi_data = any(oi != FLOAT_UNDEFINED for oi in oi_values)
        if has_oi_data:
            oi_array = np.array([oi if oi != FLOAT_UNDEFINED else np.nan for oi in oi_values])
            oi_min, oi_max = np.nanmin(oi_array), np.nanmax(oi_array)

            for t, oi, is_up in zip(time_nums, oi_values, [b.close >= b.open for b in self.bars]):
                if oi != FLOAT_UNDEFINED:
                    color = COLOR_UP if is_up else COLOR_DOWN
                    self.ax_oi.bar(t, oi, color=color, width=width)

            for period in EMA_PERIODS:
                if np.count_nonzero(~np.isnan(oi_array)) >= period:
                    ema = self.ema(oi_array, period)
                    self.ax_oi.plot(time_nums, ema, linewidth=EMA_LINEWIDTH, color=EMA_COLORS[period], alpha=EMA_ALPHA,
                                    label=f'EMA {period}')

            self.ax_oi.set_ylim(oi_min, oi_max * 1.05)
            self.ax_oi.legend(loc='upper left', fontsize=LEGEND_FONT_SIZE, facecolor=COLOR_FACE, labelcolor='white')

        locator = AutoDateLocator(minticks=X_AXIS_MIN_TICKS, maxticks=X_AXIS_MAX_TICKS)
        locator.intervald[mdates.MINUTELY] = X_AXIS_MINUTELY_INTERVALS
        formatter = DateFormatter(X_AXIS_TIME_FORMAT, tz=BELGRADE_TZ)

        for ax in [self.ax_price, self.ax_vol, self.ax_oi]:
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(formatter)
            ax.tick_params(axis='x', colors='gray', labelsize=8)

        self.fig.tight_layout()

    @staticmethod
    def ema(data, period):
        ema = np.zeros_like(data)
        k = 2 / (period + 1)
        ema[0] = data[0]
        for i in range(1, len(data)):
            if np.isnan(data[i]):
                ema[i] = ema[i - 1]
            else:
                ema[i] = data[i] * k + ema[i - 1] * (1 - k)
        return ema

    def mark_pump_start(self, pump_start_time: datetime):
        pump_start_num = mdates.date2num(pump_start_time)
        self.ax_price.axvline(pump_start_num, color=COLOR_PUMP_START, linestyle=PUMP_START_LINE_STYLE,
                              linewidth=PUMP_START_LINE_WIDTH)
        ymax = max(bar.high for bar in self.bars)
        self.ax_price.text(pump_start_num, ymax, '', color=COLOR_PUMP_START, fontsize=PUMP_START_TEXT_SIZE)

    def draw_trendline(self, trendline: Trendline):
        if not trendline or not trendline.valid:
            log("Невалидная наклонка, не будет нарисована")
            return

        x_indices = list(range(trendline.point1_index, trendline.point2_index + 1))
        y_values = [trendline.get_value_at(i) for i in x_indices]
        times = [mdates.date2num(self.bars[i].timestamp) for i in x_indices]
        self.ax_price.plot(times, y_values, color=COLOR_TRENDLINE, linestyle=TRENDLINE_STYLE, linewidth=TRENDLINE_WIDTH)
        log("Нарисована наклонка")

    def save(self, filename: str):
        import os
        os.makedirs(self.save_dir, exist_ok=True)
        full_path = os.path.join(self.save_dir, filename)
        plt.savefig(full_path, facecolor=self.fig.get_facecolor(), bbox_inches='tight')
        plt.close(self.fig)
