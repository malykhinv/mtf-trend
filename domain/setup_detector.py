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
            log("Мало данных на 15м — минимум 25 свечей нужно.")
            return None

        self.swings = StructureDetector(self.bars_15m, self.atr_15m).detect_swing_points()
        self.last = self.bars_15m[-1]
        self.prev = self.bars_15m[-2]

        return self.detect_flat_setup() or self.detect_momentum_setup()

    def detect_flat_setup(self) -> SetupSignal | None:
        d1, h4, h1 = self.mtf_states["1d"], self.mtf_states["4h"], self.mtf_states["1h"]

        if not d1.is_range:
            log("D1 не во флете — пропускаем флет-сценарий.")
            return None
        if not (h4.trend in ['flat', None] and h1.trend in ['flat', None]):
            log("4H или 1H в тренде — не флетовое состояние.")
            return None

        log("Фаза флета подтверждена. Проверяем крайние точки диапазона.")
        recent = next((s for s in reversed(self.swings)
                       if abs(s.price - d1.range_high) < self.atr_15m or
                       abs(s.price - d1.range_low) < self.atr_15m), None)
        if not recent:
            log("Не нашли свежую точку у границы диапазона.")
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
            log("Нет нужной формации: ни ложный пробой, ни закрепление с ретестом.")
            return None

        if not volume_ok:
            log("Объём слабый — нет подтверждения по реакции.")
            return None

        log("Сетап во флете найден. Готовим сигнал.")
        return self._build_signal(
            direction="long" if recent.kind == 'low' else "short",
            reference_swing=recent,
            confirmed_tfs=["1d"],
            scenario="rebound" if is_false_break else "breakout",
            text="Цена вернулась после ложного пробоя" if is_false_break else "Цена закрепилась за уровнем"
        )

    def detect_momentum_setup(self) -> SetupSignal | None:
        h4, h1 = self.mtf_states["4h"], self.mtf_states["1h"]

        if h4.trend not in ["up", "down"]:
            log("4H без направленного тренда — моментум не подтверждён.")
            return None
        if h4.is_in_correction:
            log("4H в коррекции — ждём завершения.")
            return None
        if h1.trend != h4.trend:
            log("1H в другую сторону — нет согласованности.")
            return None
        if not h1.is_in_correction:
            log("1H не в коррекции — момент входа упущен или ещё не созрел.")
            return None

        direction = "long" if h4.trend == "up" else "short"
        last_swing = next((s for s in reversed(self.swings)
                           if (direction == "long" and s.kind == "high") or
                           (direction == "short" and s.kind == "low")), None)
        if not last_swing:
            log("Нет swing-точки для подтверждения входа по тренду.")
            return None

        if not (
                direction == "long" and self.prev.close < last_swing.price < self.last.close or
                direction == "short" and self.prev.close > last_swing.price > self.last.close
        ):
            log("Цена не пробила swing в нужном направлении.")
            return None

        log("Моментум-сетап найден. Готовим сигнал.")
        return self._build_signal(
            direction=direction,
            reference_swing=last_swing,
            confirmed_tfs=["4h", "1h"],
            scenario="momentum",
            text="Вход после коррекции в тренде"
        )

    def _build_signal(
            self,
            direction: Literal['long', 'short'],
            reference_swing,
            confirmed_tfs: List[str],
            scenario: Literal['rebound', 'false_breakout', 'breakout', 'momentum'],
            text: str
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

        if not sl_candidates:
            log("Не нашли логичный уровень для стопа.")
            return None
        if not tp_candidates:
            log("Не нашли цели для тейка — нет swing-точек выше/ниже входа.")
            return None

        sl = sl_candidates[-1]
        tp = next((p for p in tp_candidates if abs(p - entry) / abs(entry - sl) >= MIN_RR), None)
        if tp is None:
            log("Нет цели с нужным соотношением риск/прибыль.")
            return None

        sl_pct = abs(entry - sl) / entry
        tp_pct = abs(tp - entry) / entry

        if sl_pct < MIN_SL_PCT:
            log("Стоп слишком близко — меньше минимального порога.")
            return None
        if tp_pct < MIN_TP_PCT:
            log("Тейк слишком близко — не даёт нужного потенциала.")
            return None

        rr = abs(tp - entry) / abs(entry - sl)
        log(f"Сигнал подтверждён. RR: {rr:.2f}, SL: {sl}, TP: {tp}")

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
