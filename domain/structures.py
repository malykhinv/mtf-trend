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
        for i in range(1, len(self.bars) - 1):
            bar = self.bars[i]
            is_high = bar.high > self.bars[i - 1].high and bar.high > self.bars[i + 1].high
            is_low = bar.low < self.bars[i - 1].low and bar.low < self.bars[i + 1].low

            if is_high:
                if direction != PriceDirection.DOWN or abs(bar.high - self.bars[i - 1].high) > self.threshold:
                    swings.append(SwingPoint(index=i, price=bar.high, type=SwingType.HIGH, confirmed=True))
                    # log(f"Swing HIGH добавлен: index={i}, price={bar.high}")
                    direction = PriceDirection.DOWN
            elif is_low:
                if direction != PriceDirection.UP or abs(bar.low - self.bars[i - 1].low) > self.threshold:
                    swings.append(SwingPoint(index=i, price=bar.low, type=SwingType.LOW, confirmed=True))
                    # log(f"Swing LOW добавлен: index={i}, price={bar.low}")
                    direction = PriceDirection.UP

        # log(f"Общее количество swing точек: {len(swings)}.")
        return swings
