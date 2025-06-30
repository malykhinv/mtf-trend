from typing import Tuple

from config.constants import TP_LOOKAHEAD_BARS, MIN_RR, STRONG_REACTION_WICK_RATIO, \
    MODERATE_REACTION_WICK_RATIO, STRONG_REACTION_VOLUME_MULTIPLIER, FLOAT_UNDEFINED, MAX_SWING_LOOKBACK_BARS, \
    MIN_SL_PCT, MIN_TP_PCT, SL_LOOKBACK_BARS
from domain.detection.flat.flat_setup_base import FlatSetupBase
from domain.models.scenario import Scenario
from domain.models.swing_type import SwingType
from utils.float_utils import is_defined, get_pct


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
        return self.has_moderate_conditions() and \
            self.volume_condition(self.candle, STRONG_REACTION_VOLUME_MULTIPLIER * self.avg_vol) and \
            self.wick_condition(self.candle, STRONG_REACTION_WICK_RATIO) and \
            self.rr_condition()

    def has_moderate_conditions(self) -> bool:
        return self.has_weak_conditions() and \
            self.volume_condition(self.candle, self.avg_vol) and \
            self.wick_condition(self.candle, MODERATE_REACTION_WICK_RATIO)

    def has_weak_conditions(self) -> bool:
        return self.flat_market_condition() and \
            self.flat_size_condition() and \
            self.flat_center_condition() and \
            self.swing_condition() and \
            self.direction_condition(self.candle, self.side) and \
            self._broke_and_returned_condition(self.candle)

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
        sl = self._define_sl(entry)

        if not get_pct(sl, entry) > MIN_SL_PCT:
            self.logw(f"Entry и SL слишком близки (entry={entry}, sl={sl}).")
            return undefined_result

        # TP по swing
        tp_swings = [
            s for s in self.swings
            if s.type == (SwingType.HIGH if self.side.is_long else SwingType.LOW)
               and s.index > self.swing.index
               and s.index - self.swing.index <= MAX_SWING_LOOKBACK_BARS
        ]

        tp = next((s.price for s in tp_swings if abs(s.price - entry) / abs(entry - sl) >= MIN_RR), None)

        # Если нет — fallback
        if not is_defined(tp):
            tp = self._define_tp_default(entry, sl)

        if not is_defined(tp) or not get_pct(tp, entry) > MIN_TP_PCT:
            self.logw(f"Entry и TP слишком близки (entry={entry}, tp={tp}).")
            return undefined_result

        rr = abs(tp - entry) / abs(entry - sl)
        if rr < MIN_RR:
            self.logw(f"RR {round(rr, 2)} < MIN_RR ({MIN_RR}).")
            return undefined_result

        self.log(f"Entry: {entry}")
        self.log(f"SL: {sl}")
        self.log(f"TP: {tp}")
        self.log(f"RR: {round(rr, 2)}")

        return entry, sl, tp, round(rr, 2)

    def _define_entry(self) -> float:
        return self.last.close

    def _define_sl(self, entry: float) -> float:
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

        # Fallback на экстремум последних баров
        if self.side.is_long:
            sl = min(b.low for b in self.bars_tf_setup[-SL_LOOKBACK_BARS:])
        else:
            sl = max(b.high for b in self.bars_tf_setup[-SL_LOOKBACK_BARS:])
        self.log("Стоп выбран по минимумам/максимумам баров.")
        return sl

    def _define_tp_default(self, entry: float, sl: float) -> float:
        # Center диапазона
        flat_center = (self.range_high + self.range_low) / 2

        if self.side.is_long and flat_center > entry:
            tp_candidate = flat_center
        elif self.side.is_short and flat_center < entry:
            tp_candidate = flat_center
        else:
            tp_candidate = FLOAT_UNDEFINED

        if is_defined(tp_candidate):
            rr_candidate = abs(tp_candidate - entry) / abs(entry - sl)
            if rr_candidate >= MIN_RR:
                self.log("TP выбран по середине диапазона (center fallback).")
                return tp_candidate
            else:
                self.logw("Midpoint не даёт достаточного RR, ищем fallback.")

        # Fallback на экстремум последних TP_LOOKAHEAD_BARS
        if self.side.is_long:
            local_high = max(b.high for b in self.bars_tf_setup[-TP_LOOKAHEAD_BARS:])
            rr_candidate = abs(local_high - entry) / abs(entry - sl)
            if rr_candidate >= MIN_RR:
                self.log("TP выбран по локальному high (fallback).")
                return local_high
        else:
            local_low = min(b.low for b in self.bars_tf_setup[-TP_LOOKAHEAD_BARS:])
            rr_candidate = abs(entry - local_low) / abs(entry - sl)
            if rr_candidate >= MIN_RR:
                self.log("TP выбран по локальному low (fallback).")
                return local_low

        self.logw("Нет подходящего fallback TP с RR >= MIN_RR.")
        return FLOAT_UNDEFINED

    # endregion
