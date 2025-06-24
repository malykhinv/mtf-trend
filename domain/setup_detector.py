from config.settings.constants import MIN_RR, MIN_SL_PCT, MIN_TP_PCT
from domain.mtf_analyzer import MTFAnalyzer
from domain.models.bar import Bar
from domain.models.mtf_state import MTFState
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

        mtf_states: Dict[str, MTFState] = {
            tf: MTFAnalyzer(
                bars=self.bars_by_tf[tf],
                timeframe=tf,
                atr=self.atr_by_tf[tf]
            ).analyze()
            for tf in self.timeframes
        }

        d1_state = mtf_states["1d"]
        h4_state = mtf_states["4h"]
        h1_state = mtf_states["1h"]

        bars_15m = self.bars_by_tf["15m"]
        atr_15m = self.atr_by_tf["15m"]
        if len(bars_15m) < 25:
            return None
        swings = StructureDetector(bars_15m, atr_15m).detect_swing_points()

        last = bars_15m[-1]
        prev = bars_15m[-2]

        # -------- ФЛЕТ-СЦЕНАРИЙ --------
        if d1_state.is_range:
            if not (h4_state.trend == 'flat' and h1_state.trend == 'flat'):
                log(f"{self.symbol}: 1D — флет, но 4H/1H — нет. Пропускаем.")
                return None

            log(f"{self.symbol} во флете. Проверка ложного пробоя и ретеста.")

            recent_swing = next((s for s in reversed(swings)
                                 if abs(s.price - d1_state.range_high) < atr_15m or
                                    abs(s.price - d1_state.range_low) < atr_15m), None)
            if not recent_swing:
                log(f"{self.symbol}: нет swing-реакции у границы.")
                return None

            volume = prev.volume
            avg_volume = sum(b.volume for b in bars_15m[-21:-1]) / 20
            volume_ok = volume > 1.2 * avg_volume

            # Проверка возврата внутрь диапазона (ложный пробой)
            if recent_swing.kind == 'high':
                is_false_break = prev.high > recent_swing.price > last.close
            else:
                is_false_break = prev.low < recent_swing.price < last.close

            # Проверка пробоя + ретеста (пробой с закреплением)
            if recent_swing.kind == 'high':
                is_break_and_retest = prev.close < recent_swing.price < last.high and last.close > recent_swing.price
            else:
                is_break_and_retest = prev.close > recent_swing.price > last.low and last.close < recent_swing.price

            if not (is_false_break or is_break_and_retest) or not volume_ok:
                log(f"{self.symbol}: нет валидной реакции у границы.")
                return None

            direction = "long" if recent_swing.kind == 'low' else "short"
            entry = last.close

            if direction == "long":
                sl_candidates = [s.price for s in swings if s.kind == "low" and s.index < recent_swing.index]
                tp_candidates = [s.price for s in swings if s.kind == "high" and s.index > recent_swing.index]
            else:
                sl_candidates = [s.price for s in swings if s.kind == "high" and s.index < recent_swing.index]
                tp_candidates = [s.price for s in swings if s.kind == "low" and s.index > recent_swing.index]

            if not sl_candidates or not tp_candidates:
                log(f"{self.symbol}: нет структурных SL/TP.")
                return None

            sl = sl_candidates[-1]
            tp = tp_candidates[0]
            sl_pct = abs(entry - sl) / entry
            tp_pct = abs(tp - entry) / entry

            if sl_pct < MIN_SL_PCT:
                log(f"{self.symbol}: SL слишком мал — {sl_pct * 100:.2f}%")
                return None

            # Проверка: SL не должен быть внутри тела текущей свечи
            if last.low < sl < last.high:
                log(f"{self.symbol}: SL внутри тела текущей свечи — пропускаем.")
                return None

            if tp_pct < MIN_TP_PCT:
                log(f"{self.symbol}: TP слишком мал — {tp_pct * 100:.2f}%")
                return None

            rr = abs(tp - entry) / abs(entry - sl)

            if rr < MIN_RR:
                log(f"{self.symbol}: RR={rr:.2f} ниже порога.")
                return None

            return SetupSignal(
                symbol=self.symbol,
                direction=cast(Literal['long', 'short'], direction),
                confidence="high",
                confirmed_timeframes=["1d"],
                rr=round(rr, 2),
                text="Цена вернулась после ложного пробоя" if is_false_break else "Цена закрепилась за уровнем",
                timestamp=last.timestamp,
                entry=entry,
                sl=sl,
                tp=tp,
                scenario="rebound" if is_false_break else "breakout"
            )

        # -------- МОМЕНТУМ-СЦЕНАРИЙ --------
        if not (h4_state.trend in ["up", "down"] and not h4_state.is_in_correction):
            log(f"{self.symbol}: нет направленного тренда на 4H.")
            return None

        if not (h1_state.trend == h4_state.trend and h1_state.is_in_correction):
            log(f"{self.symbol}: на 1H нет коррекции в тренде {h4_state.trend}.")
            return None

        direction = "long" if h4_state.trend == "up" else "short"

        last_swing = next(
            (s for s in reversed(swings)
             if (direction == "long" and s.kind == "high") or
                (direction == "short" and s.kind == "low")),
            None
        )

        if last_swing is None:
            log(f"{self.symbol}: нет swing-точки для входа.")
            return None

        confirm_candle = bars_15m[-1]
        previous_candle = bars_15m[-2]

        confirmed_break = (
            direction == "long" and previous_candle.close < last_swing.price < confirm_candle.close or
            direction == "short" and previous_candle.close > last_swing.price > confirm_candle.close
        )

        if not confirmed_break:
            log(f"{self.symbol}: свеча не подтвердила пробой swing.")
            return None

        entry = confirm_candle.close

        if direction == "long":
            sl_candidates = [s.price for s in swings if s.kind == "low" and s.index < last_swing.index]
            tp_candidates = [s.price for s in swings if s.kind == "high" and s.index > last_swing.index]
        else:
            sl_candidates = [s.price for s in swings if s.kind == "high" and s.index < last_swing.index]
            tp_candidates = [s.price for s in swings if s.kind == "low" and s.index > last_swing.index]

        if not sl_candidates or not tp_candidates:
            log(f"{self.symbol}: нет структурных SL/TP.")
            return None

        sl = sl_candidates[-1]
        tp = tp_candidates[0]
        sl_pct = abs(entry - sl) / entry
        tp_pct = abs(tp - entry) / entry

        if sl_pct < MIN_SL_PCT:
            log(f"{self.symbol}: SL слишком мал — {sl_pct * 100:.2f}%")
            return None

        if tp_pct < MIN_TP_PCT:
            log(f"{self.symbol}: TP слишком мал — {tp_pct * 100:.2f}%")
            return None

        # Проверка: SL не должен быть внутри тела текущей свечи
        if last.low < sl < last.high:
            log(f"{self.symbol}: SL внутри тела текущей свечи — пропускаем.")
            return None

        rr = abs(tp - entry) / abs(entry - sl)

        if rr < MIN_RR:
            log(f"{self.symbol}: RR={rr:.2f} ниже порога.")
            return None

        return SetupSignal(
            symbol=self.symbol,
            direction=cast(Literal['long', 'short'], direction),
            confidence="high",
            confirmed_timeframes=["4h", "1h"],
            rr=round(rr, 2),
            text=f"Вход после коррекции в тренде",
            timestamp=confirm_candle.timestamp,
            entry=entry,
            sl=sl,
            tp=tp,
            scenario="momentum"
        )
