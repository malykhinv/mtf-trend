from config.constants import MIN_RR
from domain.detection.base_setup import BaseSetup
from domain.models.timeframe import Timeframe


class MomentumSetupBase(BaseSetup):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.d1_state = self.mtf_states[Timeframe.D1]
        self.h4_state = self.mtf_states[Timeframe.H4]
        self.h1_state = self.mtf_states[Timeframe.H1]
        self.swing = self._find_recent_swing()
        self.avg_volume = sum(b.volume for b in self.bars_15m[-20:]) / 20

    def trend_condition(self) -> bool:
        if not self.d1_state.is_trend:
            self.log("✖ Нет глобального тренда на D1.")
            return False
        return True

    def impulse_condition(self) -> bool:
        if not self.h4_state.has_strong_move:
            self.log("✖ Нет импульса на H4.")
            return False
        return True

    def pullback_condition(self) -> bool:
        if not self.h1_state.is_correction:
            self.log("✖ Нет отката на H1.")
            return False
        return True

    def volume_condition(self, candle, avg_vol) -> bool:
        if not candle.volume >= avg_vol:
            self.log("✖ Объем ниже среднего.")
            return False
        return True

    def wick_condition(self, candle, max_ratio: float) -> bool:
        full_range = candle.high - candle.low
        body = abs(candle.close - candle.open)
        wick = full_range - body
        if not wick < max_ratio * full_range:
            self.log("✖ Длина хвоста свечи слишком большая.")
            return False
        return True

    def rr_condition(self) -> bool:
        if self.rr < MIN_RR:
            self.log(f"✖ RR {round(self.rr, 1)} < {round(MIN_RR, 1)}.")
            return False
        return True

    def _find_recent_swing(self):
        if self.swings:
            return self.swings[-1]
        return None
