import matplotlib
from threading import Lock

from utils.decorator import log_duration_ms
from utils.float_utils import is_defined
from utils.math_utils import most

matplotlib.use('Agg')
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

# Protects all matplotlib calls to avoid race conditions in multi-threaded usage
MATPLOTLIB_LOCK = Lock()

from matplotlib.dates import AutoDateLocator, DateFormatter

from config.constants import (
    EMA_COLORS,
    CANDLESTICK_WIDTH_MULTIPLIER,
    X_AXIS_MIN_TICKS,
    X_AXIS_MAX_TICKS,
    X_AXIS_MINUTELY_INTERVALS,
    X_AXIS_TIME_FORMAT,
    COLOR_UP,
    COLOR_DOWN,
    COLOR_TRENDLINE,
    COLOR_PUMP_START,
    TRENDLINE_STYLE,
    PUMP_START_LINE_STYLE,
    PUMP_START_TEXT_SIZE,
    EMA_ALPHA,
    LINE_WIDTH,
    COLOR_BACKGROUND,
    TIMEZONE,
    ATR_COLOR,
    SWING_COLOR_HIGH,
    SWING_COLOR_LOW,
    SWING_MARKER_SIZE, COLOR_OI, COLOR_BACKGROUND_NA,
)


class Plot:
    """
    Класс графического построения для отображения истории, сигналов, swing-поинтов и ATR/OBV.
    Позволяет строить и сохранять картинки для аналитики и Telegram.
    """
    def __init__(self,
                 symbol: str,
                 bars: List[Bar],
                 correction_swings: List[SwingPoint],
                 tf: Timeframe,
                 message: Optional[str] = None,
                 save_dir: str = 'unknown'):
        """Подготавливает окружение для построения графика."""
        self.symbol = symbol
        self.tf = tf
        self.bars = bars
        self.correction_swings = correction_swings
        self.save_dir = '.generated/plot/' + save_dir
        with MATPLOTLIB_LOCK:
            self.fig, (self.ax_price, self.ax_vol, self.ax_oi, self.ax_atr) = plt.subplots(
                4, 1,
                figsize=(14, 14),
                gridspec_kw={"height_ratios": [4, 1, 1, 1]},
                sharex=True,
                facecolor=COLOR_BACKGROUND
            )
        self.fig.patch.set_facecolor(COLOR_BACKGROUND)
        for ax in [self.ax_price, self.ax_vol, self.ax_oi, self.ax_atr]:
            ax.set_facecolor(COLOR_BACKGROUND)
            ax.tick_params(colors='gray', which='both', length=0)
            ax.grid(True, color='gray', linestyle=':', linewidth=0.5, alpha=0.25)
        title = f"{self.symbol} {self.tf.value}"
        self.ax_price.set_title(title, color='white')
        if message:
            self.ax_price.text(
                0.01, 0.85, message, transform=self.ax_price.transAxes,
                fontsize=10, color='orange', ha='left', va='bottom'
            )

    @log_duration_ms
    def generate_and_save(self, filename: str, pump_start_time: Optional[datetime], trendline: Optional[Trendline]) -> str:
        """Строит график и сохраняет изображение."""
        self.plot_main()
        if pump_start_time:
            self.mark_pump_start(pump_start_time)
        if trendline:
            self.plot_trendline(trendline)
        return self.save(filename)


    def plot_main(self):
        """
        Основная функция построения графика.
        """
        ohlc, closes, volumes, oi_values, atr_values = [], [], [], [], []

        for bar in self.bars:
            ts = bar.timestamp.astimezone(TIMEZONE)
            time_num = mdates.date2num(ts)
            ohlc.append([time_num, bar.open, bar.high, bar.low, bar.close])
            closes.append(bar.close)
            volumes.append((time_num, bar.volume, bar.close >= bar.open))
            oi_values.append(bar.oi)
            atr_values.append(bar.atr)

        time_nums = [mdates.date2num(bar.timestamp.astimezone(TIMEZONE)) for bar in self.bars]
        width = float(np.mean(np.diff(time_nums))) * CANDLESTICK_WIDTH_MULTIPLIER if len(time_nums) >= 2 else 0.0007

        candlestick_ohlc(self.ax_price, ohlc, width=width, colorup=COLOR_UP, colordown=COLOR_DOWN)

        self.plot_swings()

        vol_values = np.array([v[1] for v in volumes])
        vol_min, vol_max = vol_values.min(), vol_values.max()

        for t, vol, is_up in volumes:
            color = COLOR_UP if is_up else COLOR_DOWN
            self.ax_vol.bar(t, vol, color=color, width=width)

        self.ax_vol.set_ylim(vol_min, vol_max * 1.05)

        closes_array = np.array(closes)
        for period in [20, 50, 100, 200]:
            if len(closes_array) >= period:
                ema = self.ema(closes_array, period)
                self.ax_price.plot(time_nums, ema, linewidth=LINE_WIDTH, color=EMA_COLORS[period], alpha=EMA_ALPHA)

        has_oi_data = most(oi_values, is_defined)
        if has_oi_data:
            oi_array = np.array([oi if is_defined(oi) else np.nan for oi in oi_values])
            oi_min, oi_max = np.nanmin(oi_array), np.nanmax(oi_array)

            if oi_max > oi_min:
                normalized_oi = (oi_array - oi_min) / (oi_max - oi_min)
            else:
                normalized_oi = np.zeros_like(oi_array)

            self.ax_oi.step(time_nums, normalized_oi, where='post', color=COLOR_OI, linewidth=LINE_WIDTH)

            self.ax_oi.set_ylim(0, 1.05)
            self.ax_oi.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'{int(y * 100)}%'))
        else:
            self.ax_oi.set_facecolor(COLOR_BACKGROUND_NA)
            self.ax_oi.set_xticks([])
            self.ax_oi.set_yticks([])

        atr_line = np.array(atr_values)
        self.ax_atr.step(time_nums, atr_line, color=ATR_COLOR, linewidth=LINE_WIDTH, linestyle='-')
        atr_min, atr_max = np.nanmin(atr_line), np.nanmax(atr_line)
        self.ax_atr.set_ylim(atr_min, atr_max * 1.05)

        locator = AutoDateLocator(minticks=X_AXIS_MIN_TICKS, maxticks=X_AXIS_MAX_TICKS)
        locator.intervald[mdates.MINUTELY] = X_AXIS_MINUTELY_INTERVALS
        formatter = DateFormatter(X_AXIS_TIME_FORMAT, tz=TIMEZONE)

        for ax in [self.ax_price, self.ax_vol, self.ax_oi, self.ax_atr]:
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(formatter)
            ax.tick_params(axis='x', colors='gray', labelsize=8)

        self.fig.tight_layout()


    @staticmethod
    def ema(data: np.ndarray, period: int) -> np.ndarray:
        """Вычисляет экспоненциальную скользящую среднюю."""
        ema = np.zeros_like(data)
        k = 2 / (period + 1)
        ema[0] = data[0]
        for i in range(1, len(data)):
            if np.isnan(data[i]):
                ema[i] = ema[i - 1]
            else:
                ema[i] = data[i] * k + ema[i - 1] * (1 - k)
        return ema

    @log_duration_ms
    def mark_pump_start(self, pump_start_time: datetime):
        """Отмечает начало пампа на графике."""
        pump_start_num = mdates.date2num(pump_start_time)
        self.ax_price.axvline(pump_start_num, color=COLOR_PUMP_START, linestyle=PUMP_START_LINE_STYLE,
                              linewidth=LINE_WIDTH)
        ymax = max(bar.high for bar in self.bars)
        self.ax_price.text(pump_start_num, ymax, '', color=COLOR_PUMP_START, fontsize=PUMP_START_TEXT_SIZE)

    @log_duration_ms
    def plot_swings(self):
        """Рисует swing-точки на графике."""
        if not self.bars:
            return

        ylim = self.ax_price.get_ylim()
        y_range = ylim[1] - ylim[0]
        pixel_height = self.ax_price.get_window_extent().height
        marker_size_pts = SWING_MARKER_SIZE ** 0.5  # так как s — это площадь в pt²
        marker_height_data = y_range * (marker_size_pts / pixel_height)

        for sp in self.correction_swings:
            if sp.is_undefined or sp.timestamp is None:
                continue

            bar_time = mdates.date2num(sp.timestamp.astimezone(TIMEZONE))

            if sp.type.is_high:
                marker_y = sp.price + marker_height_data / 2  # нижняя вершина на цене
                self.ax_price.scatter(bar_time, marker_y, color=SWING_COLOR_HIGH, marker='v', s=SWING_MARKER_SIZE)
            elif sp.type.is_low:
                marker_y = sp.price - marker_height_data / 2  # верхняя вершина на цене
                self.ax_price.scatter(bar_time, marker_y, color=SWING_COLOR_LOW, marker='^', s=SWING_MARKER_SIZE)

    @log_duration_ms
    def plot_trendline(self, trendline: Trendline):
        """Рисует наклонку на графике, если она валидна."""
        if not trendline or not trendline.valid:
            log("Невалидная наклонка, не будет нарисована")
            return

        t1_num = mdates.date2num(trendline.point1_time)
        t2_num = mdates.date2num(trendline.point2_time)
        y1 = trendline.get_value_at_time(trendline.point1_time)
        y2 = trendline.get_value_at_time(trendline.point2_time)
        self.ax_price.plot(
            [t1_num, t2_num],
            [y1, y2],
            color=COLOR_TRENDLINE,
            linestyle=TRENDLINE_STYLE,
            linewidth=LINE_WIDTH,
        )
        log("Нарисована наклонка")

    @log_duration_ms
    def save(self, filename: str, *, bbox_inches: str | None = 'tight', dpi: int | None = None) -> str:
        """Сохраняет построенный график в файл."""
        import os
        os.makedirs(self.save_dir, exist_ok=True)
        full_path = os.path.join(self.save_dir, filename)
        with MATPLOTLIB_LOCK:
            plt.savefig(
                full_path,
                facecolor=self.fig.get_facecolor(),
                bbox_inches=bbox_inches,
                dpi=dpi,
            )
            plt.close(self.fig)
        return full_path
