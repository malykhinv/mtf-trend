from config.settings.constants import MIN_RR
from domain.mtf_analyzer import MTFAnalyzer
from domain.models.bar import Bar
from domain.models.mtf_state import MTFState
from domain.models.setup_signal import SetupSignal
from domain.structures import StructureDetector
from typing import Dict, List, Literal, cast
from utils.logger import log
import statistics


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

        # ---- ФЛЭТ СЦЕНАРИИ ----
        if d1_state.is_range:
            log(f"{self.symbol} во флэте (фаза 1). Проверка сценария.")
            bars_15m = self.bars_by_tf["15m"]
            atr_15m = self.atr_by_tf["15m"]

            if len(bars_15m) < 21:
                return None

            last = bars_15m[-1]
            prev = bars_15m[-2]
            before_prev = bars_15m[-3]

            avg_volume = statistics.mean([b.volume for b in bars_15m[-21:-1]])
            high_tail = prev.high - prev.close > 0.3 * atr_15m
            low_tail = prev.close - prev.low > 0.3 * atr_15m

            distance_to_high = abs(last.close - d1_state.range_high)
            distance_to_low = abs(last.close - d1_state.range_low)

            direction = None
            entry = None
            sl = None
            tp = None
            scenario = None

            if (
                before_prev.close < d1_state.range_high < prev.close < last.close and
                prev.low < d1_state.range_high and prev.volume > 1.5 * avg_volume
            ):
                direction = "long"
                entry = last.close
                sl = min(before_prev.low, prev.low) - 0.25 * atr_15m
                tp = d1_state.range_high + (d1_state.range_high - sl)
                scenario = "breakout"

            elif (
                before_prev.close > d1_state.range_low > prev.close > last.close and
                prev.high > d1_state.range_low and prev.volume > 1.5 * avg_volume
            ):
                direction = "short"
                entry = last.close
                sl = max(before_prev.high, prev.high) + 0.25 * atr_15m
                tp = d1_state.range_low - (sl - d1_state.range_low)
                scenario = "breakout"

            elif prev.high > d1_state.range_high > last.close and high_tail and prev.volume > 1.5 * avg_volume:
                direction = "short"
                entry = last.close
                sl = prev.high + 0.25 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "false_breakout"

            elif prev.low < d1_state.range_low < last.close and low_tail and prev.volume > 1.5 * avg_volume:
                direction = "long"
                entry = last.close
                sl = prev.low - 0.25 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "false_breakout"

            elif distance_to_high <= 1.5 * atr_15m and high_tail and prev.volume > 1.5 * avg_volume:
                direction = "short"
                entry = last.close
                sl = d1_state.range_high + 0.5 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "rebound"

            elif distance_to_low <= 1.5 * atr_15m and low_tail and prev.volume > 1.5 * avg_volume:
                direction = "long"
                entry = last.close
                sl = d1_state.range_low - 0.5 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "rebound"

            if direction is None:
                log(f"Цена {self.symbol} не у границ диапазона или нет реакции. Пропускаем.")
                return None

            rr = abs(tp - entry) / abs(entry - sl)
            if rr < MIN_RR:
                log(f"RR ниже порога: {rr:.2f}. Пропускаем.")
                return None

            return SetupSignal(
                symbol=self.symbol,
                direction=cast(Literal['long', 'short'], direction),
                confidence="high",
                confirmed_timeframes=["1d"],
                rr=round(rr, 2),
                text=f"{self.symbol}: {direction.upper()} ФЛЭТ-СЦЕНАРИЙ ({scenario})\nEntry: {entry}, SL: {sl}, TP: {tp}",
                timestamp=last.timestamp,
                entry=entry,
                sl=sl,
                tp=tp,
                scenario=cast(Literal['rebound'] | Literal['false_breakout'] | Literal['breakout'], scenario)
            )

        # ---- МОМЕНТУМ СЦЕНАРИЙ ----
        confirmed = []
        for tf in self.timeframes:
            s = mtf_states[tf]
            if s.trend in ["up", "down"] and not s.is_in_correction:
                confirmed.append(tf)

        if len(confirmed) < 3:
            log(f"{self.symbol}: недостаточно согласованных ТФ. Пропускаем.")
            return None

        base_trend = mtf_states[confirmed[0]].trend
        if not all(mtf_states[tf].trend == base_trend for tf in confirmed):
            log(f"{self.symbol}: таймфреймы не согласованы. Пропускаем.")
            return None

        direction = "long" if base_trend == "up" else "short"
        rr_values = [mtf_states[tf].rr_potential for tf in confirmed]
        avg_rr = round(sum(rr_values) / len(rr_values), 2)
        if avg_rr < MIN_RR:
            log(f"{self.symbol}: RR ниже порога: {avg_rr}. Пропускаем.")
            return None

        confidence = "high" if len(confirmed) == 4 else "medium"
        if confidence != "high":
            log(f"{self.symbol}: уверенность недостаточная: {confidence}. Пропускаем.")
            return None

        bars_15m = self.bars_by_tf["15m"]
        atr_15m = self.atr_by_tf["15m"]
        swings = StructureDetector(bars_15m, atr_15m).detect_swing_points()

        if len(swings) < 2:
            log(f"{self.symbol}: недостаточно swing-точек. Пропускаем.")
            return None

        last_swing = next(
            (s for s in reversed(swings)
             if (direction == "long" and s.kind == "high") or
                (direction == "short" and s.kind == "low")),
            None
        )

        if last_swing is None:
            log(f"{self.symbol}: нет swing-точки для подтверждения. Пропускаем.")
            return None

        confirm_candle = bars_15m[-1]
        previous_candle = bars_15m[-2]

        confirmed_break = (
            direction == "long" and previous_candle.close < last_swing.price < confirm_candle.close or
            direction == "short" and previous_candle.close > last_swing.price > confirm_candle.close
        )

        if not confirmed_break:
            log(f"{self.symbol}: свеча не закрылась за swing-уровнем. Нет подтверждения.")
            return None

        entry = confirm_candle.close

        if direction == "long":
            sl_swings = [s.price for s in swings if s.kind == "low" and s.index < len(bars_15m) - 1]
            tp_swings = [s.price for s in swings if s.kind == "high" and s.index > len(bars_15m) - 1]
        else:
            sl_swings = [s.price for s in swings if s.kind == "high" and s.index < len(bars_15m) - 1]
            tp_swings = [s.price for s in swings if s.kind == "low" and s.index > len(bars_15m) - 1]

        if not sl_swings or not tp_swings:
            log(f"{self.symbol}: нет структурных SL/TP. Пропускаем.")
            return None

        sl = sl_swings[-1]
        tp = tp_swings[0]

        rr = abs(tp - entry) / abs(entry - sl)
        if rr < MIN_RR:
            log(f"{self.symbol}: итоговый RR ниже порога: {rr:.2f}. Пропускаем.")
            return None

        return SetupSignal(
            symbol=self.symbol,
            direction=cast(Literal['long', 'short'], direction),
            confidence=confidence,
            confirmed_timeframes=confirmed,
            rr=rr,
            text=f"{self.symbol}: {direction.upper()} тренд\nEntry: {entry}, SL: {sl}, TP: {tp}",
            timestamp=confirm_candle.timestamp,
            entry=entry,
            sl=sl,
            tp=tp,
            scenario=cast(Literal['momentum'], "momentum")
        )
