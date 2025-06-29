from typing import Tuple

from config.constants import STRONG_REACTION_VOLUME_MULTIPLIER, STRONG_REACTION_WICK_RATIO, \
    MODERATE_REACTION_WICK_RATIO, RETEST_TOLERANCE_ATR, FLOAT_UNDEFINED
from domain.detection.momentum.momentum_setup_base import MomentumSetupBase
from domain.models.scenario import Scenario
from utils.float_utils import is_defined


class MomentumInitiation(MomentumSetupBase):
    scenario = Scenario.MOMENTUM_INITIATION

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
            self.pullback_condition() and \
            self._local_confirmation_condition() and \
            self._retest_condition()

    def _local_confirmation_condition(self) -> bool:
        """
        Проверяет, что последняя свеча закрылась выше предыдущего high для лонга
        или ниже предыдущего low для шорта.
        """
        if self.side.is_long and not self.last.close > self.prev.high:
            self.logw("Нет подтверждения: закрытие не выше предыдущего high.")
            return False

        elif self.side.is_short and not self.last.close < self.prev.low:
            self.logw("Нет подтверждения: закрытие не ниже предыдущего low.")
            return False

        return True

    def _retest_condition(self) -> bool:
        breakout_level = self.prev.high if self.side.is_long else self.prev.low
        if not abs(self.last.close - breakout_level) < RETEST_TOLERANCE_ATR * self.atr_tf_setup:
            self.logw("Нет точного ретеста зоны пробоя.")
            return False

        return True

    # endregion

    # region RR
    def define_rr(self) -> Tuple[float, float, float, float]:
        undefined_result = FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED, FLOAT_UNDEFINED
        if not self.tf_macro_trend_condition():
            self.logw(f"Нет подходящего тренда на {self.tfs.trend.value}.")
            return undefined_result

        if not self.side:
            self.logw("Невозможно задать направление сделки.")
            return undefined_result

        entry = self.last.close
        sl = self.prev.low if self.side.is_long else self.prev.high
        if not is_defined(entry - sl):
            self.logw(f"Entry и SL слишком близки (entry={entry}, sl={sl}).")
            return undefined_result

        tp = self.define_tp(entry, sl)

        if tp is None or not is_defined(tp):
            self.logw("Не удалось определить TP с достаточным RR.")
            return undefined_result

        rr = abs(tp - entry) / abs(entry - sl)
        self.log(f"Entry: {entry}")
        self.log(f"SL: {sl}")
        self.log(f"TP: {tp}")
        self.log(f"RR: {round(rr, 2)}")

        return entry, sl, tp, round(rr, 2)
    # endregion
