# utils/plot.py

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
from typing import List, Optional

from domain.models.bar import Bar
from domain.models.trendline import Trendline
from utils.logger import log


class Plot:
    def __init__(self, symbol: str, bars: List[Bar]):
        self.symbol = symbol
        self.bars = bars
        self.fig, self.ax = plt.subplots(figsize=(12, 6), facecolor='black')
        self.ax.set_facecolor('black')
        self.ax.tick_params(colors='white')
        self.ax.xaxis_date()
        self.ax.grid(True, linestyle='--', alpha=0.3)
        self.ax.set_title(symbol, color='white')
        log(f"Создан график для {symbol}")

    def _get_times_and_prices(self):
        times = [bar.timestamp for bar in self.bars]
        closes = [bar.close for bar in self.bars]
        return times, closes

    def plot_main(self, ema_series: Optional[List[float]] = None):
        times, closes = self._get_times_and_prices()
        self.ax.plot(times, closes, color='white', label='Цена', linewidth=1.5)
        log("Добавлена основная линия цены")

        if ema_series:
            if len(ema_series) != len(closes):
                log("Длина ema_series не совпадает с количеством баров — EMA не будет нарисована.")
            else:
                self.ax.plot(times, ema_series, color='violet', linestyle='--', label='EMA')
                log("Добавлена EMA линия")

        self.ax.legend()

    def mark_pump_start(self, pump_start_time: datetime):
        # Проверка и преобразование времени
        pump_start_num = mdates.date2num(pump_start_time)
        self.ax.axvline(pump_start_num, color='yellow', linestyle=':', linewidth=1)
        ymax = max(bar.high for bar in self.bars)
        self.ax.text(pump_start_num, ymax, 'Start', color='yellow', fontsize=8)
        log("Отмечена точка старта пампа")

    def mark_main_high(self, main_high_price: float):
        times, _ = self._get_times_and_prices()
        last_time = mdates.date2num(times[-1])
        self.ax.axhline(main_high_price, color='green', linestyle='--', linewidth=1)
        self.ax.text(last_time, main_high_price, 'Main High', color='green', fontsize=8)
        log("Отмечен Main High")

    def draw_trendline(self, trendline: Trendline, start_idx: int, end_idx: int):
        if not trendline.valid:
            log("Невалидная наклонка, не будет нарисована")
            return

        # X: индексы, Y: цены по трендовой
        x_indices = list(range(start_idx, end_idx + 1))
        y_values = [trendline.get_value_at(i) for i in x_indices]

        # Конвертируем индексы в datetime
        times, _ = self._get_times_and_prices()
        trend_times = [times[i] for i in x_indices]

        self.ax.plot(trend_times, y_values, color='blue', linestyle='-', linewidth=1, label='Trendline')
        log("Нарисована наклонка")

    def mark_breakout(self, breakout_idx: int):
        times, closes = self._get_times_and_prices()
        if breakout_idx >= len(times):
            log(f"Индекс пробоя {breakout_idx} выходит за пределы — отметка не будет добавлена")
            return

        self.ax.scatter(times[breakout_idx], closes[breakout_idx], color='red', s=50, zorder=5, label='Breakout')
        log("Отмечен пробой наклонки")

    def save(self, filename: str):
        # Создаем папку, если нет
        save_dir = ".generated/plot/charts"
        os.makedirs(save_dir, exist_ok=True)

        full_path = os.path.join(save_dir, filename)
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        plt.legend()
        plt.savefig(full_path, facecolor=self.fig.get_facecolor(), bbox_inches='tight')
        plt.close(self.fig)
        log(f"График сохранён: {full_path}")
