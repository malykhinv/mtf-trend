from config.settings.constants import MIN_RR
from domain.mtf_analyzer import MTFAnalyzer
from domain.models.bar import Bar
from domain.models.mtf_state import MTFState
from domain.models.setup_signal import SetupSignal
from domain.structures import StructureDetector
from typing import Dict, List, Literal
from utils.logger import log


class SetupDetector:
    def __init__(self, symbol: str, bars_by_tf: Dict[str, List[Bar]], atr_by_tf: Dict[str, float]):
        self.symbol = symbol
        self.bars_by_tf = bars_by_tf
        self.atr_by_tf = atr_by_tf
        self.timeframes = ["1d", "4h", "1h", "15m"]

    def detect(self) -> SetupSignal | None:
        log(f"Анализ актива {self.symbol}.")

        mtf_states: Dict[str, MTFState] = {}

        for tf in self.timeframes:
            analyzer = MTFAnalyzer(bars=self.bars_by_tf[tf], timeframe=tf, atr=self.atr_by_tf[tf])
            mtf_states[tf] = analyzer.analyze()

        d1_state = mtf_states["1d"]

        if d1_state.is_range:
            log(f"{self.symbol} во флэте (фаза 1). Проверка сценария отбоя, ложного пробоя или пробоя с закреплением.")
            bars_15m = self.bars_by_tf["15m"]
            atr_15m = self.atr_by_tf["15m"]
            last_bar = bars_15m[-1]
            prev_bar = bars_15m[-2]

            distance_to_high = abs(last_bar.close - d1_state.range_high)
            distance_to_low = abs(last_bar.close - d1_state.range_low)

            entry = sl = tp = direction = scenario = None

            # Пробой вверх с закреплением
            if prev_bar.close < d1_state.range_high and last_bar.close > d1_state.range_high:
                direction = "long"
                entry = last_bar.close
                sl = d1_state.range_high - 0.5 * atr_15m
                tp = entry + (entry - sl) * MIN_RR
                scenario = "breakout"
            # Пробой вниз с закреплением
            elif prev_bar.close > d1_state.range_low and last_bar.close < d1_state.range_low:
                direction = "short"
                entry = last_bar.close
                sl = d1_state.range_low + 0.5 * atr_15m
                tp = entry - (sl - entry) * MIN_RR
                scenario = "breakout"
            # Ложный пробой сверху
            elif prev_bar.high > d1_state.range_high and last_bar.close < d1_state.range_high:
                direction = "short"
                entry = last_bar.close
                sl = max(prev_bar.high, last_bar.high) + 0.25 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "false_breakout"
            # Ложный пробой снизу
            elif prev_bar.low < d1_state.range_low and last_bar.close > d1_state.range_low:
                direction = "long"
                entry = last_bar.close
                sl = min(prev_bar.low, last_bar.low) - 0.25 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "false_breakout"
            # Обычный отбой от верхней границы
            elif distance_to_high <= 1.5 * atr_15m:
                direction = "short"
                entry = last_bar.close
                sl = d1_state.range_high + 0.5 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "rebound"
            # Обычный отбой от нижней границы
            elif distance_to_low <= 1.5 * atr_15m:
                direction = "long"
                entry = last_bar.close
                sl = d1_state.range_low - 0.5 * atr_15m
                tp = (d1_state.range_high + d1_state.range_low) / 2
                scenario = "rebound"
            else:
                log(f"Цена {self.symbol} не у границ диапазона. Пропускаем.")
                return None

            rr = abs(tp - entry) / abs(entry - sl)
            if rr < MIN_RR:
                log(f"RR ниже порога для флэт-сценария: {rr:.2f}. Пропускаем.")
                return None

            log(f"Сетап во флэте: {direction.upper()} — сценарий {scenario}. RR — {rr:.2f}")
            text = f"{self.symbol}: {direction.upper()} ФЛЭТ-СЦЕНАРИЙ ({scenario})\nD1 — флэт\nEntry: {entry}, SL: {sl}, TP: {tp}, RR: {round(rr,2)}"

            return SetupSignal(
                symbol=self.symbol,
                direction=direction,
                confidence="medium",
                confirmed_timeframes=["1d"],
                rr=round(rr, 2),
                text=text.strip(),
                timestamp=last_bar.timestamp,
                entry=entry,
                sl=sl,
                tp=tp,
                scenario=scenario
            )

        confirmed = []
        for tf in self.timeframes:
            state = mtf_states[tf]
            if state.trend not in ["up", "down"]:
                continue
            if state.is_in_correction:
                if (state.trend == 'up' and state.correction_direction == 'down') or \
                   (state.trend == 'down' and state.correction_direction == 'up'):
                    continue
            confirmed.append(tf)

        if len(confirmed) < 2:
            log(f"Недостаточно согласованных таймфреймов. Пропускаем {self.symbol}.")
            return None

        base_trend = mtf_states[confirmed[0]].trend
        if not all(mtf_states[tf].trend == base_trend for tf in confirmed):
            log(f"Таймфреймы расходятся по направлению тренда. Пропускаем {self.symbol}.")
            return None

        direction: Literal['long', 'short'] = "long" if base_trend == "up" else "short"

        rr_values = [mtf_states[tf].rr_potential for tf in confirmed]
        avg_rr = round(sum(rr_values) / len(rr_values), 2)

        if avg_rr < MIN_RR:
            log(f"RR ниже порога: {avg_rr}. Пропускаем {self.symbol}.")
            return None

        confidence: Literal['low', 'medium', 'high'] = "low"
        if len(confirmed) == 3:
            confidence = "medium"
        elif len(confirmed) == 4:
            confidence = "high"

        log(f"Сетап найден: {self.symbol}, направление — {direction}, уверенность — {confidence}, RR — {avg_rr}.")

        text = f"{self.symbol}: {direction.upper()} setup\n"
        for tf in confirmed:
            rr = mtf_states[tf].rr_potential
            text += f"{tf.upper()} тренд подтверждён, RR={rr}\n"

        entry = sl = tp = scenario = None

        if confidence == "high":
            bars_15m = self.bars_by_tf["15m"]
            atr_15m = self.atr_by_tf["15m"]
            swings = StructureDetector(bars_15m, atr_15m).detect_swing_points()

            entry = bars_15m[-1].close

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

            if sl_candidates:
                sl = sl_candidates[-1].price
            else:
                sl = entry - atr_15m if direction == "long" else entry + atr_15m

            if tp_candidates:
                tp = tp_candidates[0].price
            else:
                risk = abs(entry - sl)
                tp = entry + risk * MIN_RR if direction == "long" else entry - risk * MIN_RR

            scenario = "momentum"

        return SetupSignal(
            symbol=self.symbol,
            direction=direction,
            confidence=confidence,
            confirmed_timeframes=confirmed,
            rr=avg_rr,
            text=text.strip(),
            timestamp=self.bars_by_tf['15m'][-1].timestamp,
            entry=entry,
            sl=sl,
            tp=tp,
            scenario=scenario
        )
