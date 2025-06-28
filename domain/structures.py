from domain.models.bar import Bar
from domain.models.price_direction import PriceDirection
from domain.models.swing_point import SwingPoint
from typing import List

from domain.models.swing_type import SwingType
from utils.logger import log

class StructureDetector:
    def __init__(self, bars: List[Bar], atr: float, threshold_multiplier: float = 1.5):
        self.bars = bars
        self.atr = atr
        self.threshold = atr * threshold_multiplier

    def detect_swing_points(self) -> List[SwingPoint]:
        swings = []
        direction = PriceDirection.UNDEFINED
        last_extreme = self.bars[0].high if self.bars[1].close > self.bars[0].close else self.bars[0].low

        for i in range(2, len(self.bars) - 2):
            bar = self.bars[i]
            is_high = all(bar.high > self.bars[j].high for j in [i-2, i-1, i+1, i+2])
            is_low = all(bar.low < self.bars[j].low for j in [i-2, i-1, i+1, i+2])

            if is_high and (direction != PriceDirection.DOWN or abs(bar.high - last_extreme) > self.threshold):
                swings.append(SwingPoint(index=i, price=bar.high, type=SwingType.HIGH, confirmed=True))
                direction = PriceDirection.DOWN
                last_extreme = bar.high

            elif is_low and (direction != PriceDirection.UP or abs(bar.low - last_extreme) > self.threshold):
                swings.append(SwingPoint(index=i, price=bar.low, type=SwingType.LOW, confirmed=True))
                direction = PriceDirection.UP
                last_extreme = bar.low

        log(f"Всего swing-точек найдено: {len(swings)}.")
        return swings
