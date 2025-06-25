from config.settings.constants import MIN_RR, MIN_SL_PCT, MIN_TP_PCT
from domain.mtf_analyzer import MTFAnalyzer
from domain.models.bar import Bar
from domain.models.setup_signal import SetupSignal
from domain.structures import StructureDetector
from typing import Dict, List, Literal, Optional, cast
from utils.logger import log


class SetupDetector:
    def __init__(self, symbol: str, bars_by_tf: Dict[str, List[Bar]], atr_by_tf: Dict[str, float]):
        self.symbol = symbol
        self.timeframes = ["1d", "4h", "1h", "15m"]
        self.bars_by_tf = bars_by_tf
        self.bars_15m = self.bars_by_tf["15m"]
        self.atr_by_tf = atr_by_tf
        self.atr_15m = self.atr_by_tf["15m"]
        self.bars_d1 = self.bars_by_tf["1d"]
        self.mtf_states = {
            tf: MTFAnalyzer(self.bars_by_tf[tf], tf, self.atr_by_tf[tf]).analyze()
            for tf in self.timeframes
        }
        self.swings = StructureDetector(self.bars_15m, self.atr_15m).detect_swing_points()
        self.last = self.bars_15m[-1]
        self.prev = self.bars_15m[-2]

    def detect(self) -> Optional[SetupSignal]:
        if len(self.bars_15m) < 25:
            log("Мало данных на 15м — минимум 25 свечей нужно.")
            return None

        confidence_map = {"low": 1, "medium": 2, "high": 3}
        candidates = filter(None, [
            self.flat_high(),
            self.flat_medium(),
            self.flat_low(),
            self.flat_fake_breakout(),
            self.momentum_high(),
            self.momentum_medium()
        ])
        return max(candidates, key=lambda s: confidence_map.get(s.confidence, 0), default=None)

    def flat_high(self) -> Optional[SetupSignal]:
        log("Пробуем flat_high...")
        d1, h4, h1 = self.mtf_states["1d"], self.mtf_states["4h"], self.mtf_states["1h"]
        if not d1.is_range or d1.range_high is None or d1.range_low is None:
            log("D1 не во флете или нет границ диапазона.")
            return None
        if not (h4.trend in ['flat', None] and h1.trend in ['flat', None]):
            log("4H или 1H не во флете.")
            return None

        recent = self._find_swing_near(d1.range_high, d1.range_low)
        if not recent:
            log("Нет swing рядом с границей диапазона.")
            return None

        if not self._has_strong_15m_reaction(recent):
            log("Нет сильной реакции на 15m.")
            return None

        return self._try_build_signal(
            direction="long" if recent.kind == 'low' else "short",
            reference_swing=recent,
            confirmed_tfs=["1d"],
            scenario="rebound",
            text="Флет: реакция от границы с подтверждением",
            confidence="high"
        )

    def flat_medium(self) -> Optional[SetupSignal]:
        log("Пробуем flat_medium...")
        d1 = self.mtf_states["1d"]
        if not d1.is_range or d1.range_high is None or d1.range_low is None:
            log("D1 не во флете или нет границ диапазона.")
            return None

        recent = self._find_swing_near(d1.range_high, d1.range_low)
        if not recent:
            log("Нет swing рядом с границей диапазона.")
            return None

        if not self._has_structured_moderate_15m_reaction(recent):
            log("Нет структурной реакции на 15m.")
            return None

        return self._try_build_signal(
            direction="long" if recent.kind == 'low' else "short",
            reference_swing=recent,
            confirmed_tfs=["1d"],
            scenario="rebound",
            text="Флет: слабая реакция от границы",
            confidence="medium"
        )

    def flat_low(self) -> Optional[SetupSignal]:
        log("Пробуем flat_low...")
        d1 = self.mtf_states["1d"]
        if not d1.is_range or d1.range_high is None or d1.range_low is None:
            log("D1 не во флете или нет границ диапазона.")
            return None

        recent = self._find_swing_near(d1.range_high, d1.range_low)
        if not recent:
            log("Нет swing рядом с границей диапазона.")
            return None

        return self._try_build_signal(
            direction="long" if recent.kind == 'low' else "short",
            reference_swing=recent,
            confirmed_tfs=["1d"],
            scenario="rebound",
            text="Флет: реакция у уровня без подтверждения",
            confidence="low"
        )

    def flat_fake_breakout(self) -> Optional[SetupSignal]:
        log("Пробуем flat_fake_breakout...")
        d1 = self.mtf_states["1d"]
        if not self._is_strong_flat(d1):
            log("Флет не чёткий — пропускаем.")
            return None

        broke_high = self.prev.high > d1.range_high > self.last.close
        broke_low = self.prev.low < d1.range_low < self.last.close

        if not (broke_high or broke_low):
            log("Нет ложного пробоя.")
            return None

        direction = "short" if broke_high else "long"
        level = d1.range_high if broke_high else d1.range_low

        swing = self._find_swing_near(level, level)
        if not swing:
            log("Нет swing рядом с уровнем пробоя.")
            return None

        if not self._has_strong_15m_reaction(swing):
            log("Нет сильной реакции после возврата.")
            return None

        return self._try_build_signal(
            direction=cast(Literal["short", "long"], direction),
            reference_swing=swing,
            confirmed_tfs=["1d"],
            scenario="rebound",
            text="Флет: ложный пробой и возврат",
            confidence="high",
            tp_override=(d1.range_high + d1.range_low) / 2
        )

    def momentum_high(self) -> Optional[SetupSignal]:
        log("Пробуем momentum_high...")
        h4, h1 = self.mtf_states["4h"], self.mtf_states["1h"]
        if h4.trend not in ["up", "down"]:
            log("4H без тренда.")
            return None
        if not h1.is_in_correction:
            log("1H не в коррекции.")
            return None
        if h1.trend != h4.trend:
            log("1H не совпадает с 4H по направлению.")
            return None

        last_swing = self._get_trend_swing(h4.trend)
        if not last_swing:
            log("Нет swing по тренду.")
            return None
        if not self._breakout_confirmed(last_swing, h4.trend):
            log("Нет подтверждения пробоя swing.")
            return None

        return self._try_build_signal(
            direction="long" if h4.trend == "up" else "short",
            reference_swing=last_swing,
            confirmed_tfs=["4h", "1h"],
            scenario="momentum",
            text="Моментум: вход после коррекции в тренде",
            confidence="high"
        )

    def momentum_medium(self) -> Optional[SetupSignal]:
        log("Пробуем momentum_medium...")
        h4 = self.mtf_states["4h"]
        h1 = self.mtf_states["1h"]

        if h4.trend not in ["up", "down"]:
            log("4H без тренда.")
            return None

        last_swing = self._get_trend_swing(h4.trend)
        if not last_swing:
            log("Нет swing по тренду.")
            return None

        has_correction_on_1h = h1.is_in_correction
        has_valid_swing = last_swing.confirmed
        has_reaction_on_15m = self._has_structured_moderate_15m_reaction(last_swing)

        if not (has_correction_on_1h and has_valid_swing and has_reaction_on_15m):
            log("Нет нужной структуры на 1H/15m — medium отклоняется.")
            return None

        return self._try_build_signal(
            direction="long" if h4.trend == "up" else "short",
            reference_swing=last_swing,
            confirmed_tfs=["4h"],
            scenario="momentum",
            text="Моментум: тренд есть, подтверждение слабое",
            confidence="medium"
        )

    def momentum_low(self) -> Optional[SetupSignal]:
        log("Пробуем momentum_low...")
        h4 = self.mtf_states["4h"]
        if h4.trend not in ["up", "down"]:
            log("4H без тренда.")
            return None

        return self._try_build_signal(
            direction="long" if h4.trend == "up" else "short",
            reference_swing=self.swings[-1],
            confirmed_tfs=["4h"],
            scenario="momentum",
            text="Моментум: слабое подтверждение",
            confidence="low"
        )

    def _try_build_signal(self, direction: Literal['long', 'short'], reference_swing, confirmed_tfs: List[str],
                          scenario: Literal['rebound', 'breakout', 'momentum'], text: str,
                          confidence: Literal["low", "medium", "high"], tp_override: Optional[float] = None) -> Optional[SetupSignal]:
        entry = self.last.close
        sl_candidates = [
            s.price for s in self.swings
            if s.kind == ("low" if direction == "long" else "high")
               and s.index < reference_swing.index
               and abs(entry - s.price) > 0.3 * self.atr_15m
        ]
        if not sl_candidates:
            log("Нет подходящих SL swing — используем fallback.")
            fallback_sl = (
                min(b.low for b in self.bars_15m[-15:]) if direction == "long"
                else max(b.high for b in self.bars_15m[-15:])
            )
            sl_candidates = [fallback_sl]

        sl = sl_candidates[-1]
        tp = tp_override

        if tp is None:
            tp_candidates = [
                s.price for s in self.swings
                if s.kind == ("high" if direction == "long" else "low")
                   and s.index > reference_swing.index
            ]
            tp = next((p for p in tp_candidates if abs(p - entry) / abs(entry - sl) >= MIN_RR), None)
            if tp is None:
                log("Нет swing TP с нужным RR — используем fallback.")
                fallback_tp = (
                    max(b.high for b in self.bars_15m[-20:]) if direction == "long"
                    else min(b.low for b in self.bars_15m[-20:])
                )
                tp = fallback_tp

        if not sl or not tp:
            log("SL или TP не определены — отклоняем сигнал.")
            return None

        rr = abs(tp - entry) / abs(entry - sl)
        if rr < MIN_RR:
            log(f"RR {rr:.2f} меньше минимума {MIN_RR} — отклоняем.")
            return None

        sl_pct = abs(entry - sl) / entry
        tp_pct = abs(tp - entry) / entry

        if confidence == "high" and (sl_pct < MIN_SL_PCT or tp_pct < MIN_TP_PCT):
            log("SL/TP слишком близко для high confidence — отклоняем.")
            return None

        log(f"Сигнал найден: RR={rr:.2f}, SL={sl:.5f}, TP={tp:.5f}, confidence={confidence}")

        return SetupSignal(
            symbol=self.symbol,
            direction=cast(Literal['long', 'short'], direction),
            confidence=cast(Literal["low", "medium", "high"], confidence),
            confirmed_timeframes=confirmed_tfs,
            rr=round(rr, 2),
            text=text,
            timestamp=self.last.timestamp,
            entry=entry,
            sl=sl,
            tp=tp,
            scenario=scenario
        )

    def _find_swing_near(self, high: float, low: float):
        log(f"Проверка границ диапазона: high={high}, low={low}")
        if high is None or low is None:
            log("Одна из границ диапазона — None. Прерываем поиск swing.")
            return None
        return next((s for s in reversed(self.swings)
                     if abs(s.price - high) < self.atr_15m or abs(s.price - low) < self.atr_15m), None)

    def _has_strong_15m_reaction(self, swing):
        avg_vol = sum(b.volume for b in self.bars_15m[-21:-1]) / 20
        structure_ok = self.prev.close > self.prev.open and (self.prev.high - self.prev.close) < 0.3 * (
                self.prev.high - self.prev.low)
        return self.prev.volume > 1.2 * avg_vol and abs(self.prev.close - swing.price) < self.atr_15m and structure_ok

    def _has_structured_moderate_15m_reaction(self, swing):
        avg_vol = sum(b.volume for b in self.bars_15m[-21:-1]) / 20
        volume_ok = self.prev.volume >= avg_vol
        close_near_swing = abs(self.prev.close - swing.price) < 1.2 * self.atr_15m
        good_structure = abs(self.prev.high - self.prev.close) < 0.5 * (self.prev.high - self.prev.low)
        return volume_ok and close_near_swing and good_structure

    def _get_trend_swing(self, trend: str):
        kind = "high" if trend == "up" else "low"
        return next((s for s in reversed(self.swings) if s.kind == kind), None)

    def _breakout_confirmed(self, swing, trend):
        if trend == "up":
            return self.prev.close < swing.price < self.last.close
        else:
            return self.prev.close > swing.price > self.last.close

    def _is_strong_flat(self, d1_state) -> bool:
        log("Проверка на чёткий флет...")
        if not d1_state.is_range or d1_state.range_high is None or d1_state.range_low is None:
            log("Фаза не флет или границы не заданы.")
            return False

        range_size = d1_state.range_high - d1_state.range_low
        log(f"Размер диапазона: {range_size:.5f} (ATR x2 = {2 * self.atr_15m:.5f})")
        if range_size < 2 * self.atr_15m or range_size > 10 * self.atr_15m:
            log("Диапазон слишком узкий или широкий.")
            return False

        center_now = (d1_state.range_high + d1_state.range_low) / 2
        center_ago = (self.bars_d1[-4].close + self.bars_d1[-4].open) / 2
        center_diff = abs(center_now - center_ago)
        log(f"Смещение центра диапазона: {center_diff:.5f}")
        if center_diff > self.atr_15m:
            log("Смещение центра превышает допустимый порог.")
            return False

        highs = [b.high for b in self.bars_d1[-5:]]
        lows = [b.low for b in self.bars_d1[-5:]]
        high_spread = max(highs) - min(highs)
        low_spread = max(lows) - min(lows)
        log(f"Разброс хаёв: {high_spread:.5f}, лоёв: {low_spread:.5f}")
        if high_spread > 1.5 * self.atr_15m:
            log("Хаи слишком разнородные.")
            return False
        if low_spread > 1.5 * self.atr_15m:
            log("Лои слишком разнородные.")
            return False

        log("Флет признан чётким.")
        return True