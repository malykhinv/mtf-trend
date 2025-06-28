from typing import Optional, Tuple

from config.constants import STRONG_REACTION_VOLUME_MULTIPLIER, STRONG_REACTION_WICK_RATIO, \
    MODERATE_REACTION_WICK_RATIO
from domain.detection.momentum.momentum_setup_base import MomentumSetupBase
from domain.models.scenario import Scenario


class MomentumPullback(MomentumSetupBase):
    scenario = Scenario.MOMENTUM_PULLBACK

    # region Conditions
    def has_strong_conditions(self) -> bool:
        return self.is_confirmed(
            self.has_moderate_conditions(),
            self.volume_condition(self.prev, STRONG_REACTION_VOLUME_MULTIPLIER * self.avg_volume),
            self.wick_condition(self.prev, STRONG_REACTION_WICK_RATIO),
            self.rr_condition()
        )

    def has_moderate_conditions(self) -> bool:
        return self.is_confirmed(
            self.has_weak_conditions(),
            self.volume_condition(self.prev, self.avg_volume),
            self.wick_condition(self.prev, MODERATE_REACTION_WICK_RATIO)
        )

    def has_weak_conditions(self) -> bool:
        return self.is_confirmed(
            self.trend_condition(),
            self.pullback_condition()
        )

    # endregion

    # region RR

    def define_rr(self) -> Optional[Tuple[float, float, float, float]]:
        entry = self.last.close
        sl = self.prev.low if self.side.is_long else self.prev.high

        if not self.tf1_trend_condition():
            return None

        tp = self.define_tp(entry, sl)

        if tp is None:
            self.log("✖ Не удалось определить TP с достаточным RR.")
            return None

        rr = abs(tp - entry) / abs(entry - sl)
        self.log(f"Entry: {entry}")
        self.log(f"SL: {sl}")
        self.log(f"TP: {tp}")
        self.log(f"RR: {round(rr, 2)}")

        return entry, sl, tp, round(rr, 2)
    # endregion
