from config.constants import MIN_RR, FLOAT_UNDEFINED
from domain.detection.base_setup import BaseSetup
from domain.models.side import Side
from utils.float_utils import is_defined


class MomentumSetupBase(BaseSetup):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tf_macro_state = self.mtf_states[self.tfs.macro]
        self.tf_trend_state = self.mtf_states[self.tfs.trend]
        self.tf_setup_state = self.mtf_states[self.tfs.setup]
        self.side = self._get_side()
        self.swing = self._find_recent_swing()
        self.avg_volume = sum(b.volume for b in self.bars_tf_setup[-20:]) / 20

    def trend_condition(self) -> bool:
        if not self.tf_macro_state.phase.is_uptrend and not self.tf_macro_state.phase.is_downtrend:
            self.logw(f"Нет глобального тренда на {self.tf_macro_state.timeframe.value}.")
            return False
        return True

    def pullback_condition(self) -> bool:
        if not self.tf_setup_state.is_in_correction:
            self.logw(f"Нет отката на {self.tfs.setup.value}.")
            return False
        return True

    def volume_condition(self, candle, avg_vol) -> bool:
        if not candle.volume >= avg_vol:
            self.logw("Объем ниже среднего.")
            return False
        return True

    def wick_condition(self, candle, max_ratio: float) -> bool:
        full_range = candle.high - candle.low
        body = abs(candle.close - candle.open)
        wick = full_range - body
        if not wick < max_ratio * full_range:
            self.logw("Длина хвоста свечи слишком большая.")
            return False
        return True

    def rr_condition(self) -> bool:
        if self.rr < MIN_RR:
            self.logw(f"RR {round(self.rr, 1)} < {round(MIN_RR, 1)}.")
            return False
        return True

    def tf_macro_trend_condition(self) -> bool:
        swings = self.tf_trend_state.structure
        if not swings or len(swings) < 4:
            self.logw(f"Недостаточно свингов на {self.tfs.trend.value} для анализа тренда.")
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

        self.logw(f"Трендовая структура на {self.tfs.trend.value} не подтверждена.")
        return False

    def define_tp(self, entry: float, sl: float) -> float:
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

        # Macro уровни как fallback
        tf_macro_high = self.tf_macro_state.range_high
        tf_macro_low = self.tf_macro_state.range_low

        if self.side.is_long and tf_macro_high and tf_macro_high > entry:
            rr = abs(tf_macro_high - entry) / abs(entry - sl)
            if rr >= MIN_RR:
                return tf_macro_high
        elif self.side.is_short and tf_macro_low and tf_macro_low < entry:
            rr = abs(entry - tf_macro_low) / abs(entry - sl)
            if rr >= MIN_RR:
                return tf_macro_low

        return FLOAT_UNDEFINED

    def _get_side(self) -> Side:
        tf_trend_state = self.mtf_states[self.tfs.trend]
        phase = tf_trend_state.phase
        if not phase:
            self.logw("Невозможно определить side — фаза не трендовая.")
            return Side.UNDEFINED
        elif tf_trend_state.phase.is_uptrend:
            return Side.LONG
        elif tf_trend_state.phase.is_downtrend:
            return Side.SHORT
        return Side.UNDEFINED

    def _find_recent_swing(self):
        if self.swings:
            return self.swings[-1]
        return None
