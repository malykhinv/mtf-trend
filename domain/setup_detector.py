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
        self.timeframes = ["1d", "4h", "1h", "15m"]
        self.bars_by_tf = bars_by_tf
        self.bars_15m = self.bars_by_tf["15m"]
        self.atr_by_tf = atr_by_tf
        self.atr_15m = self.atr_by_tf["15m"]
        self.mtf_states = {
            tf: MTFAnalyzer(self.bars_by_tf[tf], tf, self.atr_by_tf[tf]).analyze()
            for tf in self.timeframes
        }
        self.swings = StructureDetector(self.bars_15m, self.atr_15m).detect_swing_points()
        self.last = self.bars_15m[-1]
        self.prev = self.bars_15m[-2]

    def detect(self) -> SetupSignal | None:
        log(f"Анализ актива {self.symbol}.")

        if len(self.bars_15m) < 25:
            log("Мало данных на 15м — минимум 25 свечей нужно.")
            return None

        return self.detect_flat_setup() or self.detect_momentum_setup()

    def detect_flat_setup(self) -> SetupSignal | None:
        d1, h4, h1 = self.mtf_states["1d"], self.mtf_states["4h"], self.mtf_states["1h"]
        confidence = "high"

        if not d1.is_range:
            log("D1 не во флете.")
            confidence = "medium"
        if not (h4.trend in ['flat', None] and h1.trend in ['flat', None]):
            log("4H или 1H в тренде — состояние ближе к переходу.")
            confidence = "low"

        recent = next((s for s in reversed(self.swings)
                       if abs(s.price - d1.range_high) < self.atr_15m or
                       abs(s.price - d1.range_low) < self.atr_15m), None)
        if not recent:
            log("Нет swing-точки рядом с границей.")
            return None

        avg_volume = sum(b.volume for b in self.bars_15m[-21:-1]) / 20
        volume_ok = self.prev.volume > 1.2 * avg_volume
        is_false_break = (
            recent.kind == 'high' and self.prev.high > recent.price > self.last.close or
            recent.kind == 'low' and self.prev.low < recent.price < self.last.close
        )
        is_break_and_retest = (
            recent.kind == 'high' and self.prev.close < recent.price < self.last.high and self.last.close > recent.price or
            recent.kind == 'low' and self.prev.close > recent.price > self.last.low and self.last.close < recent.price
        )

        if not (is_false_break or is_break_and_retest):
            log("Формация слаба, просто реакция. Confidence: low.")
            confidence = "low"
        if not volume_ok:
            log("Слабый объём — confidence снижен.")
            confidence = "medium" if confidence == "high" else "low"

        return self._build_signal(
            direction=cast(Literal["long", "short"], "long" if recent.kind == 'low' else "short"),
            reference_swing=recent,
            confirmed_tfs=["1d"],
            scenario="rebound" if is_false_break else "breakout",
            text="Цена вернулась после ложного пробоя" if is_false_break else "Цена закрепилась за уровнем",
            confidence=cast(Literal["low", "medium", "high"], confidence)
        )

    def detect_momentum_setup(self) -> SetupSignal | None:
        h4, h1 = self.mtf_states["4h"], self.mtf_states["1h"]
        confidence = "high"

        if h4.trend not in ["up", "down"]:
            log("4H без тренда.")
            confidence = "low"
        if h4.is_in_correction:
            log("4H в коррекции.")
            confidence = "medium"
        if h1.trend != h4.trend:
            log("1H не совпадает с 4H.")
            confidence = "low"
        if not h1.is_in_correction:
            log("1H не в коррекции — момент входа сомнительный.")
            confidence = "medium"

        direction = "long" if h4.trend == "up" else "short"
        last_swing = next((s for s in reversed(self.swings)
                           if (direction == "long" and s.kind == "high") or
                           (direction == "short" and s.kind == "low")), None)
        if not last_swing:
            log("Нет swing для подтверждения.")
            return None

        breakout_ok = (
            direction == "long" and self.prev.close < last_swing.price < self.last.close or
            direction == "short" and self.prev.close > last_swing.price > self.last.close
        )
        if not breakout_ok:
            log("Цена не подтвердила пробой — confidence снижен.")
            confidence = "low"

        return self._build_signal(
            direction=cast(Literal["long", "short"], direction),
            reference_swing=last_swing,
            confirmed_tfs=["4h", "1h"],
            scenario="momentum",
            text="Вход после коррекции в тренде",
            confidence=cast(Literal["low", "medium", "high"], confidence)
        )

    def _build_signal(
        self,
        direction: Literal['long', 'short'],
        reference_swing,
        confirmed_tfs: List[str],
        scenario: Literal['rebound', 'false_breakout', 'breakout', 'momentum'],
        text: str,
        confidence: Literal["low", "medium", "high"]
    ) -> SetupSignal | None:
        entry = self.last.close

        sl_candidates = [
            s.price for s in self.swings
            if s.kind == ("low" if direction == "long" else "high")
            and s.index < reference_swing.index
            and abs(entry - s.price) > 0.3 * self.atr_15m
        ]
        tp_candidates = [
            s.price for s in self.swings
            if s.kind == ("high" if direction == "long" else "low")
            and s.index > reference_swing.index
        ]

        if not sl_candidates or not tp_candidates:
            log("Нет подходящих swing-точек для SL или TP.")
            return None

        sl = sl_candidates[-1]
        tp = next((p for p in tp_candidates if abs(p - entry) / abs(entry - sl) >= MIN_RR), None)

        if tp is None and confidence == "high":
            log("Нет цели с нужным RR — не годится для high confidence.")
            return None
        if tp is None:
            tp = tp_candidates[-1]

        rr = abs(tp - entry) / abs(entry - sl)
        sl_pct = abs(entry - sl) / entry
        tp_pct = abs(tp - entry) / entry

        if sl_pct < MIN_SL_PCT and confidence == "high":
            log("SL слишком близко — отклоняем.")
            return None
        if tp_pct < MIN_TP_PCT and confidence == "high":
            log("TP слишком близко — отклоняем.")
            return None

        log(f"Сигнал {confidence.upper()}. RR: {rr:.2f}, SL: {sl}, TP: {tp}")
        return SetupSignal(
            symbol=self.symbol,
            direction=cast(Literal['long', 'short'], direction),
            confidence=confidence,
            confirmed_timeframes=confirmed_tfs,
            rr=round(rr, 2),
            text=text,
            timestamp=self.last.timestamp,
            entry=entry,
            sl=sl,
            tp=tp,
            scenario=scenario
        )
