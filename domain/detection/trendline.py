# domain/detection/trendline.py

from domain.models.swing_point import SwingPoint
from domain.models.bar import Bar
from typing import List, Optional
from utils.logger import log, logw


class TrendlineBuilder:
    def __init__(self, bars: List[Bar], atr: float):
        self.bars = bars
        self.atr = atr
        self.point1: Optional[SwingPoint] = None
        self.point2: Optional[SwingPoint] = None
        self.k: Optional[float] = None
        self.b: Optional[float] = None
        self.swing_points: List[SwingPoint] = []
        self.is_valid: bool = False

    def build(self, point1: SwingPoint, point2: SwingPoint) -> None:
        """
        Строит наклонку через две swing точки.
        """
        self.point1 = point1
        self.point2 = point2

        if point2.index == point1.index:
            logw("Невозможно построить наклонку: одинаковые индексы точек.")
            self.k = None
            self.b = None
            self.is_valid = False
            return

        self.k = (point2.price - point1.price) / (point2.index - point1.index)
        self.b = point1.price - self.k * point1.index
        self.is_valid = True

        log(f"Наклонка построена: k={self.k:.5f}, b={self.b:.5f}")

    def update(self, new_point: SwingPoint) -> None:
        """
        Добавляет новую точку в список и перестраивает наклонку динамически.
        """
        if not self.point1:
            logw("point1 не установлен, не могу обновить наклонку.")
            return

        self.swing_points.append(new_point)
        self.dynamic_rebuild()

    def dynamic_rebuild(self) -> None:
        """
        Перестраивает наклонку, выбирая самую «жёсткую» линию (максимально близкую к свечам).
        """
        if len(self.swing_points) < 1:
            logw("Недостаточно точек для перестройки наклонки.")
            return

        best_point = self.point2

        for candidate in self.swing_points:
            if candidate.index <= self.point1.index:
                continue
            temp_k = (candidate.price - self.point1.price) / (candidate.index - self.point1.index)
            temp_b = self.point1.price - temp_k * self.point1.index

            is_valid = True
            for i in range(self.point1.index + 1, candidate.index):
                trendline_price = temp_k * i + temp_b
                close_price = self.bars[i].close
                diff = close_price - trendline_price

                if diff > self.atr:
                    is_valid = False
                    break

            if is_valid:
                if not best_point or candidate.price < best_point.price:
                    best_point = candidate

        if best_point and best_point != self.point2:
            self.build(self.point1, best_point)
            log(f"Наклонка перестроена динамически с новой точкой: index={best_point.index}, price={best_point.price:.5f}")

    def is_breakout(self, close_price: float, index: int) -> bool:
        """
        Проверяет, закрылась ли цена выше наклонной.
        """
        if self.k is None or self.b is None:
            logw("Наклонка не определена для проверки пробоя.")
            return False

        trendline_price = self.k * index + self.b
        is_above = close_price > trendline_price

        log(f"Проверка пробоя: close={close_price:.5f}, линия={trendline_price:.5f}, выше={is_above}")
        return is_above

    def count_touches(self) -> int:
        """
        Считает количество касаний наклонки по истории с допуском по ATR.
        """
        if self.k is None or self.b is None:
            logw("Наклонка не определена для подсчёта касаний.")
            return 0

        touch_count = 0

        for i, bar in enumerate(self.bars):
            trendline_price = self.k * i + self.b
            diff = abs(bar.close - trendline_price)

            if diff < 0.2 * self.atr:  # Допуск по ATR (20%)
                touch_count += 1

        log(f"Касаний наклонки найдено: {touch_count}")
        return touch_count

    def get_line_params(self) -> Optional[tuple[float, float]]:
        """
        Возвращает параметры линии (k, b) для внешнего использования или отрисовки.
        """
        return (self.k, self.b) if self.k is not None and self.b is not None else None
