from typing import Tuple

from config.constants import STRONG_REACTION_VOLUME_MULTIPLIER, STRONG_REACTION_WICK_RATIO, \
    MODERATE_REACTION_WICK_RATIO, FLOAT_UNDEFINED
from domain.detection.momentum.momentum_setup_base import MomentumSetupBase
from domain.models.scenario import Scenario


class MomentumPullback(MomentumSetupBase):
    scenario = Scenario.MOMENTUM_PULLBACK

    # region Conditions
    def has_strong_conditions(self) -> bool:
        return self.has_moderate_conditions() and \
            self.volume_condition(self.prev, STRONG_REACTION_VOLUME_MULTIPLIER * self.avg_volume) and \
            self.wick_condition(self.prev, STRONG_REACTION_WICK_RATIO) and \
            self.rr_condition()

    def has_moderate_conditions(self) -> bool:
        return self.has_weak_conditions() and \
            self.volume_condition(self.prev, self.avg_volume) and \
            self.wick_condition(self.prev, MODERATE_REACTION_WICK_RATIO)

    def has_weak_conditions(self) -> bool:
        return self.trend_condition() and \
            self.pullback_condition()

    # endregion

    # region RR

    def define_rr(self) -> Tuple[float, float, float, float]:
        undefined_result = FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED

        entry = self.last.close
        sl = self.prev.low if self.side.is_long else self.prev.high

        if abs(entry - sl) < 1e-6:
            self.logw(f"Entry и SL слишком близки (entry={entry}, sl={sl}).")
            return undefined_result

        if not self.tf_macro_trend_condition():
            return undefined_result

        tp = self.define_tp(entry, sl)

        if tp is None:
            self.logw("Не удалось определить TP с достаточным RR.")
            return undefined_result

        rr = abs(tp - entry) / abs(entry - sl)
        self.log(f"Entry: {entry}")
        self.log(f"SL: {sl}")
        self.log(f"TP: {tp}")
        self.log(f"RR: {round(rr, 2)}")

        return entry, sl, tp, round(rr, 2)
    # endregion
