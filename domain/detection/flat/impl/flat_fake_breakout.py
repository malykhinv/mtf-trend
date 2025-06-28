from typing import Tuple

from config.constants import TP_LOOKAHEAD_BARS, MIN_RR, STRONG_REACTION_WICK_RATIO, \
    MODERATE_REACTION_WICK_RATIO, STRONG_REACTION_VOLUME_MULTIPLIER, FLOAT_UNDEFINED
from domain.detection.flat.flat_setup_base import FlatSetupBase
from domain.models.scenario import Scenario
from domain.models.swing_type import SwingType


class FlatFakeBreakout(FlatSetupBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.candle = self.prev
        self.avg_vol = self._avg_volume()
        self.swing = self._find_swing_near_level()
        self.side = self._get_side(self.swing)
        self.message = f"{self.scenario.value} : {self.confidence.value.capitalize()}"

    scenario = Scenario.FLAT_FAKE_BREAKOUT

    # region Conditions
    def has_strong_conditions(self) -> bool:
        return self.is_confirmed(
            self.has_moderate_conditions(),
            self.volume_condition(self.candle, STRONG_REACTION_VOLUME_MULTIPLIER * self.avg_vol),
            self.wick_condition(self.candle, STRONG_REACTION_WICK_RATIO),
            self.rr_condition()
        )

    def has_moderate_conditions(self) -> bool:
        return self.is_confirmed(
            self.has_weak_conditions(),
            self.volume_condition(self.candle, self.avg_vol),
            self.wick_condition(self.candle, MODERATE_REACTION_WICK_RATIO),
        )

    def has_weak_conditions(self) -> bool:
        return self.is_confirmed(
            self.flat_market_condition(),
            self.flat_size_condition(),
            self.flat_center_condition(),
            self.swing_condition(),
            self.direction_condition(self.candle, self.side),
            self._broke_and_returned_condition(self.candle)
        )

    def _broke_and_returned_condition(self, candle) -> bool:
        if (candle.high > self.range_high > candle.close) or \
           (candle.low < self.range_low < candle.close):
            return True

        self.logw("Не было ложного пробоя.")
        return False

    # endregion

    # region RR
    def define_rr(self) -> Tuple[float, float, float, float]:
        undefined_result = FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED
        entry = self._define_entry()
        sl = self._define_sl()
        if abs(entry - sl) < 1e-6:
            self.logw(f"Entry и SL слишком близки (entry={entry}, sl={sl}).")
            return undefined_result

        tp = self._define_tp(entry, sl)
        rr = abs(tp - entry) / abs(entry - sl)

        return entry, sl, tp, round(rr, 2)

    def _define_entry(self) -> float:
        return self.last.close

    def _define_sl(self) -> float:
        return self.candle.low if self.side.is_long else self.candle.high

    def _define_tp(self, entry: float, sl: float) -> float:
        if abs(entry - sl) < 1e-6:
            self.logw(f"Entry и SL слишком близки (entry={entry}, sl={sl}).")
            return FLOAT_UNDEFINED

        tp_swings = [
            s for s in self.swings
            if s.type == (SwingType.HIGH if self.side.is_long else SwingType.LOW) and s.index > self.swing.index
        ]
        tp = next((s.price for s in tp_swings if abs(s.price - entry) / abs(entry - sl) >= MIN_RR), None)
        if not tp:
            tp = max(b.high for b in self.bars_tf_setup[-TP_LOOKAHEAD_BARS:]) \
                if self.side.is_long else min(b.low for b in self.bars_tf_setup[-TP_LOOKAHEAD_BARS:])
        return tp

    # endregion
