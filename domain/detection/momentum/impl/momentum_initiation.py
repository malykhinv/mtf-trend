from typing import Optional, Tuple

from config.constants import STRONG_REACTION_VOLUME_MULTIPLIER, STRONG_REACTION_WICK_RATIO, \
    MODERATE_REACTION_WICK_RATIO, RETEST_TOLERANCE_ATR
from domain.detection.momentum.momentum_setup_base import MomentumSetupBase
from domain.models.scenario import Scenario
from domain.models.side import Side


class MomentumInitiation(MomentumSetupBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.side = self._get_side()

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
        if not abs(self.last.close - breakout_level) < RETEST_TOLERANCE_ATR * self.atr_tf_setup:
            self.log("✖ Нет точного ретеста зоны пробоя.")
            return False

        return True

    # endregion

    # region RR
    def define_rr(self) -> Optional[Tuple[float, float, float, float]]:

        if not self.tf1_trend_condition():
            self.log(f"✖ Нет подходящего тренда на {self.tfs.trend.value}.")
            return None

        if not self.side:
            self.log("✖ Невозможно задать направление сделки.")
            return None

        entry = self.last.close
        sl = self.prev.low if self.side.is_long else self.prev.high

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

    def _get_side(self) -> Optional[Side]:
        tf_trend_state = self.mtf_states[self.tfs.trend]
        phase = tf_trend_state.phase
        if not phase:
            self.log("✖ Невозможно определить side — фаза не трендовая.")
            return None
        elif tf_trend_state.phase.is_uptrend:
            return Side.LONG
        elif tf_trend_state.phase.is_downtrend:
            return Side.SHORT
        return None