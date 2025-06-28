from config.constants import MIN_RR
from domain.detection.base_setup import BaseSetup
from typing import Optional


class MomentumSetupBase(BaseSetup):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tf0_state = self.mtf_states[self.tfs[0]]
        self.tf1_state = self.mtf_states[self.tfs[1]]
        self.tf2_state = self.mtf_states[self.tfs[2]]
        self.swing = self._find_recent_swing()
        self.avg_volume = sum(b.volume for b in self.bars_tf3[-20:]) / 20

    def trend_condition(self) -> bool:
        if not self.tf0_state.is_trend:
            self.log("✖ Нет глобального тренда на D1.")
            return False
        return True

    def impulse_condition(self) -> bool:
        if not self.tf1_state.has_strong_move:
            self.log(f"✖ Нет импульса на {self.tfs[1].value}.")
            return False
        return True

    def pullback_condition(self) -> bool:
        if not self.tf2_state.is_correction:
            self.log(f"✖ Нет отката на {self.tfs[2].value}.")
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

    def tf1_trend_condition(self) -> bool:
        swings = self.tf1_state.swings
        if not swings or len(swings) < 4:
            self.log(f"✖ Недостаточно свингов на {self.tfs[1].value} для анализа тренда.")
            return False

        hh_count = 0
        hl_count = 0
        prev_high = None
        prev_low = None

        for swing in swings:
            if swing.type.is_high:
                if prev_high is None or swing.price > prev_high:
                    hh_count += 1
                    prev_high = swing.price
            elif swing.type.is_low:
                if prev_low is None or swing.price > prev_low:
                    hl_count += 1
                    prev_low = swing.price

        ll_count = 0
        lh_count = 0
        prev_low_s = None
        prev_high_s = None

        for swing in swings:
            if swing.type.is_low:
                if prev_low_s is None or swing.price < prev_low_s:
                    ll_count += 1
                    prev_low_s = swing.price
            elif swing.type.is_high:
                if prev_high_s is None or swing.price < prev_high_s:
                    lh_count += 1
                    prev_high_s = swing.price

        if hh_count >= 2 and hl_count >= 2:
            return True
        if ll_count >= 2 and lh_count >= 2:
            return True

        self.log(f"✖ Трендовая структура на {self.tfs[1].value} не подтверждена.")
        return False

    def define_tp(self, entry: float, sl: float) -> Optional[float]:
        swings = self.swings

        # Swing как главный вариант
        candidates = []
        for s in swings:
            if self.side.is_long and s.type.is_high and s.price > entry:
                rr = abs(s.price - entry) / abs(entry - sl)
                if rr >= MIN_RR:
                    candidates.append(s)
            elif self.side.is_short and s.type.is_low and s.price < entry:
                rr = abs(entry - s.price) / abs(entry - sl)
                if rr >= MIN_RR:
                    candidates.append(s)

        if candidates:
            if self.side.is_long:
                return min(candidates, key=lambda s: s.price).price
            elif self.side.is_short:
                return max(candidates, key=lambda s: s.price).price

        # D1 уровни как fallback
        tf0_high = self.tf0_state.range_high
        tf0_low = self.tf0_state.range_low

        if self.side.is_long and tf0_high and tf0_high > entry:
            rr = abs(tf0_high - entry) / abs(entry - sl)
            if rr >= MIN_RR:
                return tf0_high
        elif self.side.is_short and tf0_low and tf0_low < entry:
            rr = abs(entry - tf0_low) / abs(entry - sl)
            if rr >= MIN_RR:
                return tf0_low

        return None

    def _find_recent_swing(self):
        if self.swings:
            return self.swings[-1]
        return None
