from abc import ABC
from typing import Optional
from domain.detection.base_setup import BaseSetup
from domain.models.bar import Bar
from domain.models.swing_point import SwingPoint
from domain.models.side import Side
from domain.models.timeframe import Timeframe
from config.constants import (
    SWING_PROXIMITY_ATR_MULTIPLIER,
    FLAT_MAX_CENTER_SHIFT_ATR,
    STRONG_REACTION_VOLUME_MULTIPLIER,
    TOUCH_DISTANCE_ATR,
    CANDLE_AVG_VOLUME_PERIOD
)


class FlatSetupBase(BaseSetup, ABC):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.d1_state = self.mtf_states[Timeframe.D1]
        self.range_high = self.d1_state.range_high
        self.range_low = self.d1_state.range_low
        self.swing = self._find_swing_near_level()

    # region Conditions
    def flat_market_condition(self) -> bool:
        if not (self.d1_state.is_range and self.range_high and self.range_low):
            self.log("Не флет — отклоняем.")
            return False

        return True

    def flat_size_condition(self) -> bool:
        size = self.range_high - self.range_low
        min_size = 2 * self.atr_15m
        max_size = 10 * self.atr_15m
        if not min_size <= size <= max_size:
            self.log(f"Диапазон невалиден — отклоняем.")
            return False

        return True

    def flat_center_condition(self) -> bool:
        current_center = (self.range_high + self.range_low) / 2
        past_bar = self.bars_by_tf[Timeframe.D1][-4]
        past_center = (past_bar.close + past_bar.open) / 2
        shift = abs(current_center - past_center)
        if not shift < FLAT_MAX_CENTER_SHIFT_ATR * self.atr_15m:
            self.log(f"Центр диапазона смещён — отклоняем.")
            return False

        return True

    def swing_condition(self):
        if not self.swing:
            self.log(f"Нет swing у границы — отклоняем.")
            return False

        return True

    def volume_condition(self, candle, avg_vol) -> bool:
        if self.confidence.is_strong:
            return candle.volume > STRONG_REACTION_VOLUME_MULTIPLIER * avg_vol
        if self.confidence.is_moderate:
            return candle.volume >= avg_vol
        return True

    @staticmethod
    def wick_condition(candle: Bar, height_multiplier: float) -> bool:
        full_range = candle.high - candle.low
        body = abs(candle.close - candle.open)
        wick = full_range - body
        if not wick < height_multiplier * full_range:
            return False

        return True

    def distance_condition(self, close: float, level: float, atr_multiplier: float = 1.0) -> bool:
        if not abs(close - level) < atr_multiplier * self.atr_15m:
            return False

        return True

    def touch_condition(self, price: float, level: float) -> bool:
        if not abs(price - level) < TOUCH_DISTANCE_ATR * self.atr_15m:
            return False

        return True

    @staticmethod
    def direction_condition(candle, side: Side) -> bool:
        if not candle.close > candle.open if side.is_long else candle.close < candle.open:
            return False

        return True

    def returned_inside_range_condition(self, close: float) -> bool:
        if not self.range_low < close < self.range_high:
            return False

        return True

    # endregion

    def _find_swing_near_level(self) -> Optional[SwingPoint]:
        for s in reversed(self.swings):
            if abs(s.price - self.range_high) < SWING_PROXIMITY_ATR_MULTIPLIER * self.atr_15m:
                return s
            if abs(s.price - self.range_low) < SWING_PROXIMITY_ATR_MULTIPLIER * self.atr_15m:
                return s
        return None

    def _find_swing_near(self, level: float) -> Optional[SwingPoint]:
        for s in reversed(self.swings):
            if abs(s.price - level) < self.atr_15m * SWING_PROXIMITY_ATR_MULTIPLIER:
                return s
        return None

    def _avg_volume(self) -> float:
        return sum(b.volume for b in self.bars_15m[-CANDLE_AVG_VOLUME_PERIOD - 1:-1]) / CANDLE_AVG_VOLUME_PERIOD

    @staticmethod
    def _get_side(swing: SwingPoint) -> Side:
        return Side.LONG if swing.type.is_low else Side.SHORT
