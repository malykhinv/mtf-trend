import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from matplotlib.ticker import FuncFormatter
from mplfinance.original_flavor import candlestick_ohlc
from datetime import datetime
from typing import List, Optional

from domain.models.bar import Bar
from domain.models.swing_point import SwingPoint
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
    LEGEND_FONT_SIZE, COLOR_FACE, BELGRADE_TZ, ATR_COLOR,
)

class Plot:
    def __init__(self,
                 symbol: str,
                 bars: List[Bar],
                 correction_swings: List[SwingPoint],
                 tf: Timeframe,
                 message: Optional[str] = None,
                 save_dir=".generated/plot/charts"):
        self.symbol = symbol
        self.bars = bars
        self.correction_swings = correction_swings
        self.save_dir = save_dir
        self.fig, (self.ax_price, self.ax_vol, self.ax_oi, self.ax_atr) = plt.subplots(
            4, 1,
            figsize=(14, 14),
            gridspec_kw={"height_ratios": [4, 1, 1, 1]},
            sharex=True,
            facecolor=COLOR_FACE
        )
        self.fig.patch.set_facecolor(COLOR_FACE)

        for ax in [self.ax_price, self.ax_vol, self.ax_oi, self.ax_atr]:
            ax.set_facecolor(COLOR_FACE)
            ax.tick_params(colors='gray', which='both', length=0)
            ax.grid(True, color='gray', linestyle=':', linewidth=0.5, alpha=0.25)

        self.ax_price.set_title(f"{symbol} ({tf.value})", color='white', fontsize=14)

        if message:
            self.ax_price.text(
                0.01, 0.85, message, transform=self.ax_price.transAxes,
                fontsize=10, color='orange', ha='left', va='bottom'
            )

    def generate_and_save(self, filename: str, pump_start_time: Optional[datetime], trendline: Optional) -> str:
        self.plot_main()
        if pump_start_time:
            self.mark_pump_start(pump_start_time)
        if trendline:
            self.plot_trendline(trendline)
        return self.save(filename)

    def plot_main(self):
        ohlc, closes, volumes, oi_values, atr_values = [], [], [], [], []

        for bar in self.bars:
            ts_belgrade = bar.timestamp.astimezone(BELGRADE_TZ)
            time_num = mdates.date2num(ts_belgrade)
            ohlc.append([time_num, bar.open, bar.high, bar.low, bar.close])
            closes.append(bar.close)
            volumes.append((time_num, bar.volume, bar.close >= bar.open))
            oi_values.append(bar.oi)
            atr_values.append(bar.atr)

        time_nums = [mdates.date2num(bar.timestamp.astimezone(BELGRADE_TZ)) for bar in self.bars]
        width = float(np.mean(np.diff(time_nums))) * CANDLE_WIDTH_MULTIPLIER if len(time_nums) >= 2 else 0.0007

        candlestick_ohlc(self.ax_price, ohlc, width=width, colorup=COLOR_UP, colordown=COLOR_DOWN)

        self.plot_swings()

        vol_values = np.array([v[1] for v in volumes])
        vol_min, vol_max = vol_values.min(), vol_values.max()

        for t, vol, is_up in volumes:
            color = COLOR_UP if is_up else COLOR_DOWN
            self.ax_vol.bar(t, vol, color=color, width=width)

        self.ax_vol.set_ylim(vol_min, vol_max * 1.05)
        self.ax_vol.set_ylabel("Volume", color='gray', fontsize=8)

        closes_array = np.array(closes)
        for period in EMA_PERIODS:
            if len(closes_array) >= period:
                ema = self.ema(closes_array, period)
                self.ax_price.plot(time_nums, ema, linewidth=EMA_LINEWIDTH, color=EMA_COLORS[period],
                                   alpha=EMA_ALPHA, label=f'EMA {period}')

        self.ax_price.legend(loc='upper left', fontsize=LEGEND_FONT_SIZE, facecolor=COLOR_FACE, labelcolor='white')

        has_oi_data = any(oi != FLOAT_UNDEFINED for oi in oi_values)
        if has_oi_data:
            oi_array = np.array([oi if oi != FLOAT_UNDEFINED else np.nan for oi in oi_values])
            oi_min, oi_max = np.nanmin(oi_array), np.nanmax(oi_array)

            if oi_max > oi_min:
                normalized_oi = (oi_array - oi_min) / (oi_max - oi_min)
            else:
                normalized_oi = np.zeros_like(oi_array)

            for t, norm_oi, is_up in zip(time_nums, normalized_oi, [b.close >= b.open for b in self.bars]):
                if not np.isnan(norm_oi):
                    color = COLOR_UP if is_up else COLOR_DOWN
                    self.ax_oi.bar(t, norm_oi, color=color, width=width)

            self.ax_oi.set_ylim(0, 1.05)
            self.ax_oi.set_ylabel("OI", color='gray', fontsize=8)
            self.ax_oi.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'{int(y * 100)}%'))

        atr_line = np.array(atr_values)
        self.ax_atr.plot(time_nums, atr_line, color=ATR_COLOR, linewidth=1, linestyle='-')
        atr_min, atr_max = np.nanmin(atr_line), np.nanmax(atr_line)
        self.ax_atr.set_ylim(atr_min, atr_max * 1.05)
        self.ax_atr.set_ylabel("ATR", color='gray', fontsize=8)

        locator = AutoDateLocator(minticks=X_AXIS_MIN_TICKS, maxticks=X_AXIS_MAX_TICKS)
        locator.intervald[mdates.MINUTELY] = X_AXIS_MINUTELY_INTERVALS
        formatter = DateFormatter(X_AXIS_TIME_FORMAT, tz=BELGRADE_TZ)

        for ax in [self.ax_price, self.ax_vol, self.ax_oi, self.ax_atr]:
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

    def plot_swings(self):
        for sp in self.correction_swings:
            if sp.is_undefined:
                continue
            if sp.index < 0 or sp.index >= len(self.bars):
                continue

            bar_time = mdates.date2num(self.bars[sp.index].timestamp.astimezone(BELGRADE_TZ))

            if sp.type.is_high:
                self.ax_price.scatter(bar_time, sp.price, color='blue', marker='^', s=80)
            elif sp.type.is_low:
                self.ax_price.scatter(bar_time, sp.price, color='orange', marker='v', s=80)

    def plot_trendline(self, trendline: Trendline):
        if not trendline or not trendline.valid:
            log("Невалидная наклонка, не будет нарисована")
            return

        x_indices = list(range(trendline.point1_index, trendline.point2_index + 1))
        y_values = [trendline.get_value_at(i) for i in x_indices]
        times = [mdates.date2num(self.bars[i].timestamp) for i in x_indices]
        self.ax_price.plot(times, y_values, color=COLOR_TRENDLINE, linestyle=TRENDLINE_STYLE, linewidth=TRENDLINE_WIDTH)
        log("Нарисована наклонка")

    def save(self, filename: str) -> str:
        import os
        os.makedirs(self.save_dir, exist_ok=True)
        full_path = os.path.join(self.save_dir, filename)
        plt.savefig(full_path, facecolor=self.fig.get_facecolor(), bbox_inches='tight')
        plt.close(self.fig)
        return full_path
