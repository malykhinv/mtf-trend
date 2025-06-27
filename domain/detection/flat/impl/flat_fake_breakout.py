from typing import Optional, Tuple

from config.constants import TP_LOOKAHEAD_BARS, MIN_RR, SL_LOOKBACK_BARS, STRONG_REACTION_WICK_RATIO, \
    MODERATE_REACTION_WICK_RATIO
from domain.detection.flat.flat_setup_base import FlatSetupBase
from domain.models.setup_signal import SetupSignal
from domain.models.scenario import Scenario
from domain.models.swing_type import SwingType
from domain.models.timeframe import Timeframe


class FlatFakeBreakout(FlatSetupBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.candle = self.prev
        self.avg_vol = self._avg_volume()
        self.swing = self._find_swing_near_level()
        self.side = self._get_side(self.swing)

    scenario = Scenario.FLAT_FAKE_BREAKOUT

    # region Conditions
    def has_strong_conditions(self) -> bool:
        return self.is_confirmed(
            self.has_moderate_conditions(),
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
            self.broke_and_returned_condition(self.candle),
            self.direction_condition(self.candle, self.side)
        )

    def broke_and_returned_condition(self, candle) -> bool:
        if (candle.high > self.range_high and candle.close < self.range_high) or \
           (candle.low < self.range_low and candle.close > self.range_low):
            self.log("Был выход за границу и возврат внутрь диапазона.")
            return True

        self.log("Не было ложного пробоя — отклоняем.")
        return False

    # endregion

    # region RR
    def define_rr(self) -> Optional[Tuple[float, float, float, float]]:
        entry = self.last.close

        sl = self.candle.low if self.side.is_long else self.candle.high

        tp_swings = [
            s for s in self.swings
            if s.type == (SwingType.HIGH if self.side.is_long else SwingType.LOW) and s.index > self.swing.index
        ]
        tp = next((s.price for s in tp_swings if abs(s.price - entry) / abs(entry - sl) >= MIN_RR), None)
        if not tp:
            tp = max(b.high for b in self.bars_15m[-TP_LOOKAHEAD_BARS:]) \
                if self.side.is_long else min(b.low for b in self.bars_15m[-TP_LOOKAHEAD_BARS:])

        rr = abs(tp - entry) / abs(entry - sl)

        return entry, sl, tp, round(rr, 2)

    # endregion
