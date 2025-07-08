from typing import Tuple

from config.constants import STRONG_REACTION_VOLUME_MULTIPLIER, STRONG_REACTION_WICK_RATIO, \
    MODERATE_REACTION_WICK_RATIO, RETEST_TOLERANCE_ATR, FLOAT_UNDEFINED, MIN_SL_PCT, MIN_TP_PCT, MIN_RR
from domain.detection.momentum.momentum_setup import MomentumSetup
from domain.models.scenario import Scenario
from utils.float_utils import is_defined, get_pct


class MomentumInitiation(MomentumSetup):
    scenario = Scenario.MOMENTUM_INITIATION

    # region Conditions
    def has_strong_conditions(self) -> bool:
        return self.has_moderate_conditions() and \
            self.volume_condition(self.prev, STRONG_REACTION_VOLUME_MULTIPLIER * self.avg_volume) and \
            self.wick_condition(self.prev, STRONG_REACTION_WICK_RATIO) and \
            (self._retest_condition() or
            self._local_confirmation_condition()) and \
            self.rr_condition()

    def has_moderate_conditions(self) -> bool:
        return self.has_weak_conditions() and \
            self.tf_trend_condition() and \
            self.volume_condition(self.prev, self.avg_volume) and \
            self.wick_condition(self.prev, MODERATE_REACTION_WICK_RATIO)

    def has_weak_conditions(self) -> bool:
        return self.trend_condition() and \
            self.pullback_condition()

    def _local_confirmation_condition(self) -> bool:
        """
        Проверяет, что последняя свеча закрылась выше предыдущего high для лонга
        или ниже предыдущего low для шорта.
        """
        if self.side.is_long and not self.last.close > self.prev.high:
            self.logw(f"Нет подтверждения: закрытие не выше предыдущего high {self.prev.high}.")
            return False

        elif self.side.is_short and not self.last.close < self.prev.low:
            self.logw(f"Нет подтверждения: закрытие не ниже предыдущего low ({self.prev.low}).")
            return False

        return True

    def _retest_condition(self) -> bool:
        breakout_level = self.prev.high if self.side.is_long else self.prev.low
        if not abs(self.last.close - breakout_level) < RETEST_TOLERANCE_ATR * self.atr_tf_setup:
            self.logw(f"Нет точного ретеста зоны пробоя ({breakout_level}).")
            return False

        return True

    # endregion

    # region RR
    def define_rr(self) -> Tuple[float, float, float, float]:
        undefined_result = FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED

        if not self.side:
            self.logw("Невозможно задать направление сделки.")
            return undefined_result

        entry = self.last.close

        # --- SL: ближайший swing ---
        swing_candidates = [
            s for s in reversed(self.swings)
            if (self.side.is_long and s.type.is_low and s.price < entry) or
               (self.side.is_short and s.type.is_high and s.price > entry)
        ]

        if not swing_candidates:
            self.logw("Нет подходящего swing для SL.")
            return undefined_result

        swing_for_sl = swing_candidates[0]
        sl = swing_for_sl.price

        # Проверка на достаточную дистанцию SL (используем деление на entry)
        sl_distance_pct = get_pct(entry, sl)
        if sl_distance_pct < MIN_SL_PCT:
            self.logw(f"Entry и SL слишком близки (entry={entry}, sl={sl}).")
            return undefined_result

        # --- TP: swings по приоритету ---
        tp = FLOAT_UNDEFINED
        rr = FLOAT_UNDEFINED

        tf_candidates = [self.tfs.setup, self.tfs.trend, self.tfs.macro]

        for tf in tf_candidates:
            swings_tf = self.mtf_states[tf].structure
            swing_targets = [
                s for s in swings_tf
                if (self.side.is_long and s.type.is_high and s.price > entry) or
                   (self.side.is_short and s.type.is_low and s.price < entry)
            ]

            for target in swing_targets:
                rr_candidate = abs(target.price - entry) / abs(entry - sl)
                if rr_candidate >= MIN_RR:
                    tp = target.price
                    rr = round(rr_candidate, 2)
                    break

            if is_defined(tp):
                break

        # --- Fallback на локальный экстремум последних 20 баров ---
        if not is_defined(tp):
            local_bars = self.bars_tf_setup[-20:]

            if self.side.is_long:
                highs_above_entry = [b.high for b in local_bars if b.high > entry]
                if highs_above_entry:
                    local_high = max(highs_above_entry)
                    rr_candidate = abs(local_high - entry) / abs(entry - sl)
                    if rr_candidate >= MIN_RR:
                        tp = local_high
                        rr = round(rr_candidate, 2)
            else:
                lows_below_entry = [b.low for b in local_bars if b.low < entry]
                if lows_below_entry:
                    local_low = min(lows_below_entry)
                    rr_candidate = abs(entry - local_low) / abs(entry - sl)
                    if rr_candidate >= MIN_RR:
                        tp = local_low
                        rr = round(rr_candidate, 2)

        if not is_defined(tp):
            self.logw("Нет подходящего swing или экстремума для TP с RR >= MIN_RR.")
            return undefined_result

        self.log(f"Entry: {entry}")
        self.log(f"SL: {sl}")
        self.log(f"TP: {tp}")
        self.log(f"RR: {round(rr, 2)}")

        return entry, sl, tp, round(rr, 2)

    # endregion
