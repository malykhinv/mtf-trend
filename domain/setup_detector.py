from config.settings.constants import MIN_RR, MIN_SL_PCT, MIN_TP_PCT
from domain.mtf_analyzer import MTFAnalyzer
from domain.models.bar import Bar
from domain.models.setup_signal import SetupSignal
from domain.structures import StructureDetector
from typing import Dict, List, Literal, cast
from utils.logger import log


class SetupDetector:
    def __init__(self, symbol: str, bars_by_tf: Dict[str, List[Bar]], atr_by_tf: Dict[str, float]):
        self.symbol = symbol
        self.bars_by_tf = bars_by_tf
        self.atr_by_tf = atr_by_tf
        self.timeframes = ["1d", "4h", "1h", "15m"]

    def detect(self) -> SetupSignal | None:
        log(f"Анализ актива {self.symbol}.")

        self.mtf_states = {
            tf: MTFAnalyzer(self.bars_by_tf[tf], tf, self.atr_by_tf[tf]).analyze()
            for tf in self.timeframes
        }

        self.bars_15m = self.bars_by_tf["15m"]
        self.atr_15m = self.atr_by_tf["15m"]

        if len(self.bars_15m) < 25:
            return None

        self.swings = StructureDetector(self.bars_15m, self.atr_15m).detect_swing_points()
        self.last = self.bars_15m[-1]
        self.prev = self.bars_15m[-2]

        return self.detect_flat_setup() or self.detect_momentum_setup()

    def detect_flat_setup(self) -> SetupSignal | None:
        d1, h4, h1 = self.mtf_states["1d"], self.mtf_states["4h"], self.mtf_states["1h"]

        if not d1.is_range or not (h4.trend in ['flat', None] and h1.trend in ['flat', None]):
            return None

        log(f"{self.symbol} во флете. Проверка ложного пробоя и ретеста.")
        recent = next((s for s in reversed(self.swings)
                       if abs(s.price - d1.range_high) < self.atr_15m or
                       abs(s.price - d1.range_low) < self.atr_15m), None)
        if not recent:
            return None

        volume_ok = self.prev.volume > 1.2 * sum(b.volume for b in self.bars_15m[-21:-1]) / 20

        is_false_break = (
                recent.kind == 'high' and self.prev.high > recent.price > self.last.close or
                recent.kind == 'low' and self.prev.low < recent.price < self.last.close
        )
        is_break_and_retest = (
                recent.kind == 'high' and self.prev.close < recent.price < self.last.high and self.last.close > recent.price or
                recent.kind == 'low' and self.prev.close > recent.price > self.last.low and self.last.close < recent.price
        )
        if not (is_false_break or is_break_and_retest) or not volume_ok:
            return None

        return self._build_signal("long" if recent.kind == 'low' else "short", recent, ["1d"],
                                  "rebound" if is_false_break else "breakout",
                                  "Цена вернулась после ложного пробоя" if is_false_break else "Цена закрепилась за уровнем")

    def detect_momentum_setup(self) -> SetupSignal | None:
        h4, h1 = self.mtf_states["4h"], self.mtf_states["1h"]

        if h4.trend not in ["up", "down"] or h4.is_in_correction:
            return None
        if h1.trend != h4.trend or not h1.is_in_correction:
            return None

        direction = "long" if h4.trend == "up" else "short"
        last_swing = next((s for s in reversed(self.swings)
                           if (direction == "long" and s.kind == "high") or
                           (direction == "short" and s.kind == "low")), None)
        if not last_swing:
            return None

        if not (
                direction == "long" and self.prev.close < last_swing.price < self.last.close or
                direction == "short" and self.prev.close > last_swing.price > self.last.close
        ):
            return None

        return self._build_signal(direction, last_swing, ["4h", "1h"], "momentum", "Вход после коррекции в тренде")

    def _build_signal(
            self,
            direction: Literal['long', 'short'],
            pivot,
            confirmed_tfs: List[str],
            scenario: Literal['rebound', 'false_breakout', 'breakout', 'momentum'],
            text: str
    ) -> SetupSignal | None:
        entry = self.last.close
        sl_candidates = [s.price for s in self.swings if
                         s.kind == ("low" if direction == "long" else "high") and s.index < pivot.index]
        tp_candidates = [s.price for s in self.swings if
                         s.kind == ("high" if direction == "long" else "low") and s.index > pivot.index]

        if not sl_candidates or not tp_candidates:
            return None

        sl = sl_candidates[-1]
        tp = tp_candidates[0]
        sl_pct = abs(entry - sl) / entry
        tp_pct = abs(tp - entry) / entry

        if sl_pct < MIN_SL_PCT or tp_pct < MIN_TP_PCT:
            return None

        rr = abs(tp - entry) / abs(entry - sl)
        if rr < MIN_RR:
            return None

        return SetupSignal(
            symbol=self.symbol,
            direction=cast(Literal['long', 'short'], direction),
            confidence="high",
            confirmed_timeframes=confirmed_tfs,
            rr=round(rr, 2),
            text=text,
            timestamp=self.last.timestamp,
            entry=entry,
            sl=sl,
            tp=tp,
            scenario=scenario
        )
