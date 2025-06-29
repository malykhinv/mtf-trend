from typing import Tuple

from config.constants import TP_LOOKAHEAD_BARS, MIN_RR, SL_LOOKBACK_BARS, STRONG_REACTION_WICK_RATIO, \
    MODERATE_REACTION_WICK_RATIO, TOUCH_DISTANCE_ATR, STRONG_REACTION_VOLUME_MULTIPLIER, FLOAT_UNDEFINED, \
    MAX_SWING_LOOKBACK_BARS, MIN_SL_PCT, MIN_TP_PCT
from domain.detection.flat.flat_setup_base import FlatSetupBase
from domain.models.scenario import Scenario
from domain.models.swing_type import SwingType
from utils.float_utils import is_defined, get_pct


class FlatBounce(FlatSetupBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.candle = self.prev
        self.avg_vol = self._avg_volume()
        self.swing = self._find_swing_near_level()
        self.side = self._get_side(self.swing)

    scenario = Scenario.FLAT_BOUNCE

    # region Conditions
    def has_strong_conditions(self) -> bool:
        return self.has_moderate_conditions() and \
            self.volume_condition(self.candle, STRONG_REACTION_VOLUME_MULTIPLIER * self.avg_vol) and \
            self.wick_condition(self.candle, STRONG_REACTION_WICK_RATIO) and \
            self.distance_condition(self.candle.close, self.swing.price) and \
            self.rr_condition()

    def has_moderate_conditions(self) -> bool:
        return self.has_weak_conditions() and \
            self.swing_condition() and \
            self.volume_condition(self.candle, self.avg_vol) and \
            self.wick_condition(self.candle, MODERATE_REACTION_WICK_RATIO) and \
            self.distance_condition(self.candle.close, self.swing.price, TOUCH_DISTANCE_ATR)

    def has_weak_conditions(self) -> bool:
        return self.flat_market_condition() and \
            self.flat_size_condition() and \
            self.flat_center_condition() and \
            (self.touch_condition(self.candle.low, self.swing.price) or
            self.touch_condition(self.candle.high, self.swing.price)) and \
            self.returned_inside_range_condition(self.candle.close) and \
            self.direction_condition(self.candle, self.side)

    # endregion

    # region RR
    def define_rr(self) -> Tuple[float, float, float, float]:
        undefined_result = FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED
        entry = self.last.close
        sl = self._define_sl_swings(entry)

        if not is_defined(sl):
            sl = self._define_sl_default()

        tp = self._define_tp_swings(entry, sl)
        if not is_defined(tp):
            tp = self._define_tp_default()

        if not get_pct(sl, entry) > MIN_SL_PCT:
            self.logw(f"Entry и SL слишком близки (entry={entry}, sl={sl}).")
            return undefined_result

        if not get_pct(tp, entry) > MIN_TP_PCT:
            self.logw(f"Entry и TP слишком близки (entry={entry}, tp={tp}).")
            return undefined_result

        rr = abs(tp - entry) / abs(entry - sl)
        self.log(f"RR рассчитан: {round(rr, 2)}")

        return entry, sl, tp, round(rr, 2)

    def _define_sl_swings(self, entry: float) -> float:
        sl_swings = [
            s for s in self.swings
            if s.type != self.swing.type
               and s.index < self.swing.index
               and self.swing.index - s.index <= MAX_SWING_LOOKBACK_BARS
               and abs(entry - s.price) > 0.3 * self.atr_tf_setup
        ]
        if sl_swings:
            self.log("Стоп по прошлым свингам найден.")
            return sl_swings[-1].price
        self.log("Подходящих прошлых свингов для стопа не найдено.")
        return FLOAT_UNDEFINED

    def _define_sl_default(self) -> float:
        if self.side.is_long:
            sl = min(b.low for b in self.bars_tf_setup[-SL_LOOKBACK_BARS:])
        else:
            sl = max(b.high for b in self.bars_tf_setup[-SL_LOOKBACK_BARS:])
        self.log("Стоп выбран по минимумам/максимумам баров.")
        return sl

    def _define_tp_swings(self, entry: float, sl: float) -> float:
        tp_swings = [
            s for s in self.swings
            if s.type == (SwingType.HIGH if self.side.is_long else SwingType.LOW)
               and s.index > self.swing.index
               and s.index - self.swing.index <= MAX_SWING_LOOKBACK_BARS
        ]
        for s in tp_swings:
            if abs(s.price - entry) / abs(entry - sl) >= MIN_RR:
                self.log("Тейк по свингам найден.")
                return s.price
        self.log("Подходящих свингов для тейка не найдено.")
        return FLOAT_UNDEFINED

    def _define_tp_default(self) -> float:
        if self.side.is_long:
            tp = max(b.high for b in self.bars_tf_setup[-TP_LOOKAHEAD_BARS:])
        else:
            tp = min(b.low for b in self.bars_tf_setup[-TP_LOOKAHEAD_BARS:])
        self.log("Тейк выбран по экстремумам баров.")
        return tp

    # endregion
