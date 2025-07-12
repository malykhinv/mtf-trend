import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from mplfinance.original_flavor import candlestick_ohlc
from datetime import datetime
from typing import List

from domain.models.bar import Bar
from domain.models.trendline import Trendline
from utils.logger import log

class Plot:
    def __init__(self, symbol: str, bars: List[Bar]):
        self.symbol = symbol
        self.bars = bars
        self.fig, (self.ax_price, self.ax_vol) = plt.subplots(
            2, 1,
            figsize=(12, 8),
            gridspec_kw={"height_ratios": [3, 1]},
            sharex=True,
            facecolor='black'
        )
        self.ax_price.set_facecolor('black')
        self.ax_vol.set_facecolor('black')
        self.ax_price.tick_params(colors='white')
        self.ax_vol.tick_params(colors='white')
        self.ax_price.grid(True, linestyle='--', alpha=0.3)
        self.ax_vol.grid(True, linestyle='--', alpha=0.3)
        self.ax_price.set_title(symbol, color='white')

    def plot_main(self):
        ohlc = []
        volumes = []

        for i, bar in enumerate(self.bars):
            time_num = mdates.date2num(bar.timestamp)
            ohlc.append([time_num, bar.open, bar.high, bar.low, bar.close])
            volumes.append((time_num, bar.volume, bar.close >= bar.open))

        candlestick_ohlc(self.ax_price, ohlc, width=0.0007, colorup='lime', colordown='red')

        for t, vol, is_up in volumes:
            color = 'lime' if is_up else 'red'
            self.ax_vol.bar(t, vol, color=color, width=0.0007)

        self.ax_price.xaxis_date()
        self.ax_price.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
        self.fig.autofmt_xdate()

    def mark_pump_start(self, pump_start_time: datetime):
        pump_start_num = mdates.date2num(pump_start_time)
        self.ax_price.axvline(pump_start_num, color='yellow', linestyle=':', linewidth=1)
        ymax = max(bar.high for bar in self.bars)
        self.ax_price.text(pump_start_num, ymax, 'Start', color='yellow', fontsize=8)
        log("Отмечена точка старта пампа")

    def mark_main_high(self, main_high_price: float):
        times = [mdates.date2num(bar.timestamp) for bar in self.bars]
        last_time = times[-1]
        self.ax_price.axhline(main_high_price, color='green', linestyle='--', linewidth=1)
        self.ax_price.text(last_time, main_high_price, 'Main High', color='green', fontsize=8)
        log("Отмечен Main High")

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
        self.ax_price.plot(times, y_values, color='blue', linestyle='-', linewidth=1, label='Trendline')
        log("Нарисована наклонка")

    def save(self, filename: str):
        save_dir = ".generated/plot/charts"
        import os
        os.makedirs(save_dir, exist_ok=True)
        full_path = os.path.join(save_dir, filename)
        plt.savefig(full_path, facecolor=self.fig.get_facecolor(), bbox_inches='tight')
        plt.close(self.fig)
