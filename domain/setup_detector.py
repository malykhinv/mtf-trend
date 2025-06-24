from config.settings.constants import MIN_RR
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

        # ---- ФЛЭТ СЦЕНАРИИ ----
        if d1_state.is_range:
            log(f"{self.symbol} во флэте (фаза 1). Проверка сценария.")
            bars_15m = self.bars_by_tf["15m"]
            atr_15m = self.atr_by_tf["15m"]

            if len(bars_15m) < 3:
                return None

            last = bars_15m[-1]
            prev = bars_15m[-2]
            before_prev = bars_15m[-3]

            distance_to_high = abs(last.close - d1_state.range_high)
            distance_to_low = abs(last.close - d1_state.range_low)

            # Проверка на breakout вверх
            if (
                    before_prev.close < d1_state.range_high < prev.close < last.close
                    and prev.low < d1_state.range_high
            ):
                direction = "long"
                entry = last.close
                sl = d1_state.range_high - 0.5 * atr_15m
                tp = entry + (entry - sl) * MIN_RR
                scenario = "breakout"

            # Проверка на breakout вниз
            elif (
                    before_prev.close > d1_state.range_low > prev.close > last.close
                    and prev.high > d1_state.range_low
            ):
                direction = "short"
                entry = last.close
                sl = d1_state.range_low + 0.5 * atr_15m
                tp = entry - (sl - entry) * MIN_RR
                scenario = "breakout"

            # Остальные сценарии без изменений
            elif prev.high > d1_state.range_high > last.close:
                direction = "short"
                entry = last.close
                sl = max(prev.high, last.high) + 0.25 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "false_breakout"
            elif prev.low < d1_state.range_low < last.close:
                direction = "long"
                entry = last.close
                sl = min(prev.low, last.low) - 0.25 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "false_breakout"
            elif distance_to_high <= 1.5 * atr_15m:
                direction = "short"
                entry = last.close
                sl = d1_state.range_high + 0.5 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "rebound"
            elif distance_to_low <= 1.5 * atr_15m:
                direction = "long"
                entry = last.close
                sl = d1_state.range_low - 0.5 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "rebound"
            else:
                log(f"Цена {self.symbol} не у границ диапазона. Пропускаем.")
                return None

            rr = abs(tp - entry) / abs(entry - sl)
            if rr < MIN_RR:
                log(f"RR ниже порога: {rr:.2f}. Пропускаем.")
                return None

            log(f"Сетап во флэте: {direction.upper()} — {scenario}. RR={rr:.2f}")
            return SetupSignal(
                symbol=self.symbol,
                direction=cast(Literal['long', 'short'], direction),
                confidence="medium",
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

        if len(confirmed) < 2:
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

        confidence = "low"
        if len(confirmed) == 3:
            confidence = "medium"
        elif len(confirmed) == 4:
            confidence = "high"

        bars_15m = self.bars_by_tf["15m"]
        atr_15m = self.atr_by_tf["15m"]
        swings = StructureDetector(bars_15m, atr_15m).detect_swing_points()

        if len(swings) < 2:
            log(f"{self.symbol}: недостаточно swing-точек. Пропускаем.")
            return None

        # Последняя swing против тренда должна быть пробита (подтверждение на 15м)
        last_swing = next(
            (s for s in reversed(swings)
             if (direction == "long" and s.kind == "high") or
             (direction == "short" and s.kind == "low")),
            None
        )

        if last_swing is None:
            log(f"{self.symbol}: нет swing-точки для подтверждения. Пропускаем.")
            return None

        confirm_price = bars_15m[-1].close
        breakout = (
                direction == "long" and confirm_price > last_swing.price or
                direction == "short" and confirm_price < last_swing.price
        )

        if not breakout:
            log(f"{self.symbol}: swing {last_swing.kind} не пробит — нет подтверждения коррекции.")
            return None

        entry = confirm_price

        sl_candidates = [
            s for s in swings
            if (direction == "long" and s.kind == "low" and s.index < len(bars_15m) - 1) or
               (direction == "short" and s.kind == "high" and s.index < len(bars_15m) - 1)
        ]
        tp_candidates = [
            s for s in swings
            if (direction == "long" and s.kind == "high" and s.index > len(bars_15m) - 1) or
               (direction == "short" and s.kind == "low" and s.index > len(bars_15m) - 1)
        ]

        if not sl_candidates or not tp_candidates:
            log(f"{self.symbol}: нет SL/TP точек. Пропускаем.")
            return None

        sl = sl_candidates[-1].price
        tp = tp_candidates[0].price
        scenario = "momentum"

        return SetupSignal(
            symbol=self.symbol,
            direction=cast(Literal['long', 'short'], direction),
            confidence=cast(Literal['low', 'medium', 'high'], confidence),
            confirmed_timeframes=confirmed,
            rr=avg_rr,
            text=f"{self.symbol}: {direction.upper()} тренд\nEntry: {entry}, SL: {sl}, TP: {tp}",
            timestamp=bars_15m[-1].timestamp,
            entry=entry,
            sl=sl,
            tp=tp,
            scenario=cast(Literal['momentum'], scenario)
        )
