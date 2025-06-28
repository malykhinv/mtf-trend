from abc import ABC
from typing import Optional
from domain.detection.base_setup import BaseSetup
from domain.models.bar import Bar
from domain.models.swing_point import SwingPoint
from domain.models.side import Side
from config.constants import (
    SWING_PROXIMITY_ATR_MULTIPLIER,
    FLAT_MAX_CENTER_SHIFT_ATR,
    TOUCH_DISTANCE_ATR,
    CANDLE_AVG_VOLUME_PERIOD
)


class FlatSetupBase(BaseSetup, ABC):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tf0_state = self.mtf_states[self.tfs.macro]
        self.range_high = self.tf0_state.range_high
        self.range_low = self.tf0_state.range_low
        self.swing = self._find_swing_near_level()

    # region Conditions
    def flat_market_condition(self) -> bool:
        if not (self.tf0_state.is_range and self.range_high and self.range_low):
            self.logw("Не флет.")
            return False
        return True

    def flat_size_condition(self) -> bool:
        size = self.range_high - self.range_low
        min_size = 2 * self.atr_tf_setup
        max_size = 10 * self.atr_tf_setup
        if not min_size <= size <= max_size:
            self.logw("Диапазон слишком мал или велик.")
            return False
        return True

    def flat_center_condition(self) -> bool:
        current_center = (self.range_high + self.range_low) / 2
        past_bar = self.bars_by_tf[self.tfs.macro][-4]
        past_center = (past_bar.close + past_bar.open) / 2
        shift = abs(current_center - past_center)
        if not shift < FLAT_MAX_CENTER_SHIFT_ATR * self.atr_tf_setup:
            self.logw("Центр диапазона смещен.")
            return False

        return True

    def swing_condition(self):
        if not self.swing:
            self.logw("Нет свинга около границы.")
            return False

        return True

    def volume_condition(self, candle, avg_vol) -> bool:
        if not candle.volume >= avg_vol:
            self.logw("Объем свечи ниже среднего.")
            return False
        return True

    def wick_condition(self, candle: Bar, height_multiplier: float) -> bool:
        full_range = candle.high - candle.low
        body = abs(candle.close - candle.open)
        wick = full_range - body
        if not wick < height_multiplier * full_range:
            self.logw("Длина хвоста свечи превышает лимит.")
            return False

        return True

    def distance_condition(self, close: float, level: float, atr_multiplier: float = 1.0) -> bool:
        if not abs(close - level) < atr_multiplier * self.atr_tf_setup:
            self.logw("Цена закрытия далеко от уровня.")
            return False

        return True

    def touch_condition(self, price: float, level: float) -> bool:
        if not abs(price - level) < TOUCH_DISTANCE_ATR * self.atr_tf_setup:
            self.logw("Цена не коснулась уровня.")
            return False

        return True

    def direction_condition(self, candle, side: Side) -> bool:
        if not candle.close > candle.open if side.is_long else candle.close < candle.open:
            self.logw("Направление закрытия свечи не совпадает с предполагаемой стороной.")
            return False

        return True

    def returned_inside_range_condition(self, close: float) -> bool:
        if not self.range_low < close < self.range_high:
            self.logw("Цена не вернулась внутрь диапазона.")
            return False

        return True

    # endregion

    def _find_swing_near_level(self) -> SwingPoint:
        for s in reversed(self.swings):
            if abs(s.price - self.range_high) < SWING_PROXIMITY_ATR_MULTIPLIER * self.atr_tf_setup:
                return s
            if abs(s.price - self.range_low) < SWING_PROXIMITY_ATR_MULTIPLIER * self.atr_tf_setup:
                return s
        return SwingPoint.undefined()

    def _find_swing_near(self, level: float) -> SwingPoint:
        for s in reversed(self.swings):
            if abs(s.price - level) < self.atr_tf_setup * SWING_PROXIMITY_ATR_MULTIPLIER:
                return s
        return SwingPoint.undefined()

    def _avg_volume(self) -> float:
        return sum(b.volume for b in self.bars_tf_setup[-CANDLE_AVG_VOLUME_PERIOD - 1:-1]) / CANDLE_AVG_VOLUME_PERIOD

    def _get_side(self, swing: SwingPoint) -> Side:
        if not swing:
            self.logw("Swing не задан.")
            return Side.UNDEFINED

        return Side.LONG if swing.type.is_low else Side.SHORT
