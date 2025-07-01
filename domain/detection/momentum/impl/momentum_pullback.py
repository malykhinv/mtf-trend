from typing import Tuple

from config.constants import STRONG_REACTION_VOLUME_MULTIPLIER, STRONG_REACTION_WICK_RATIO, \
    MODERATE_REACTION_WICK_RATIO, FLOAT_UNDEFINED, MIN_SL_PCT, MIN_TP_PCT, MIN_RR
from domain.detection.momentum.momentum_setup import MomentumSetup
from domain.models.scenario import Scenario
from utils.float_utils import is_defined, get_pct


class MomentumPullback(MomentumSetup):
    scenario = Scenario.MOMENTUM_PULLBACK

    # region Conditions
    def has_strong_conditions(self) -> bool:
        return self.has_moderate_conditions() and \
            self.volume_condition(self.prev, STRONG_REACTION_VOLUME_MULTIPLIER * self.avg_volume) and \
            self.wick_condition(self.prev, STRONG_REACTION_WICK_RATIO) and \
            self.rr_condition()

    def has_moderate_conditions(self) -> bool:
        return self.has_weak_conditions() and \
            self.tf_trend_condition() and \
            self.volume_condition(self.prev, self.avg_volume) and \
            self.wick_condition(self.prev, MODERATE_REACTION_WICK_RATIO)

    def has_weak_conditions(self) -> bool:
        return self.trend_condition() and \
            self.pullback_condition()

    # endregion

    # region RR

    def define_rr(self) -> Tuple[float, float, float, float]:
        undefined_result = FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED

        if not self.side:
            self.logw("Невозможно определить направление сделки.")
            return undefined_result

        # Entry
        entry = self.last.close

        # SL по swing
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

        # Проверка минимальной дистанции SL
        sl_distance_pct = get_pct(entry, sl)
        if sl_distance_pct < MIN_SL_PCT:
            self.logw(f"Entry и SL слишком близки (entry={entry}, sl={sl}).")
            return undefined_result

        # TP — приоритет swing по структурам (setup → trend → macro)
        tp = FLOAT_UNDEFINED

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
                    break

            if is_defined(tp):
                break

        # Fallback: экстремум последних 20 баров
        if not is_defined(tp):
            local_bars = self.bars_tf_setup[-20:]

            if self.side.is_long:
                highs_above_entry = [b.high for b in local_bars if b.high > entry]
                if highs_above_entry:
                    local_high = max(highs_above_entry)
                    rr_candidate = abs(local_high - entry) / abs(entry - sl)
                    if rr_candidate >= MIN_RR:
                        tp = local_high
            else:
                lows_below_entry = [b.low for b in local_bars if b.low < entry]
                if lows_below_entry:
                    local_low = min(lows_below_entry)
                    rr_candidate = abs(entry - local_low) / abs(entry - sl)
                    if rr_candidate >= MIN_RR:
                        tp = local_low

        if not is_defined(tp):
            self.logw("Нет подходящего swing или экстремума для TP с RR >= MIN_RR.")
            return undefined_result

        # Проверка минимальной дистанции TP
        tp_distance_pct = get_pct(tp, entry)
        if tp_distance_pct < MIN_TP_PCT:
            self.logw(f"Entry и TP слишком близки (entry={entry}, tp={tp}).")
            return undefined_result

        # 7️⃣ Финальный расчет RR
        final_rr = abs(tp - entry) / abs(entry - sl)

        self.log(f"Entry: {entry}")
        self.log(f"SL: {sl}")
        self.log(f"TP: {tp}")
        self.log(f"RR: {round(final_rr, 2)}")

        return entry, sl, tp, round(final_rr, 2)

    # endregion
