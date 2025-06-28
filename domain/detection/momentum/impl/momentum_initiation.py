from typing import Optional, Tuple

from config.constants import STRONG_REACTION_VOLUME_MULTIPLIER, STRONG_REACTION_WICK_RATIO, \
    MODERATE_REACTION_WICK_RATIO, RETEST_TOLERANCE_ATR
from domain.detection.momentum.momentum_setup_base import MomentumSetupBase
from domain.models.scenario import Scenario


class MomentumInitiation(MomentumSetupBase):
    scenario = Scenario.MOMENTUM_INITIATION

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
            self.pullback_condition(),
            self._local_confirmation_condition(),
            self._retest_condition()
        )

    def _local_confirmation_condition(self) -> bool:
        """
        Проверяет, что последняя свеча закрылась выше предыдущего high для лонга
        или ниже предыдущего low для шорта.
        """
        if self.side.is_long and not self.last.close > self.prev.high:
            self.log("✖ Нет подтверждения: закрытие не выше предыдущего high.")
            return False

        elif self.side.is_short and not self.last.close < self.prev.low:
            self.log("✖ Нет подтверждения: закрытие не ниже предыдущего low.")
            return False

        return True

    def _retest_condition(self) -> bool:
        breakout_level = self.prev.high if self.side.is_long else self.prev.low
        if not abs(self.last.close - breakout_level) < RETEST_TOLERANCE_ATR * self.atr_15m:
            self.log("✖ Нет точного ретеста зоны пробоя.")
            return False

        return True

    # endregion

    # region RR
    def define_rr(self) -> Optional[Tuple[float, float, float, float]]:
        entry = self.last.close
        sl = self.prev.low if self.side.is_long else self.prev.high

        if not self.h4_trend_condition():
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
