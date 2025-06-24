from config.settings.constants import MIN_RR
from domain.mtf_analyzer import MTFAnalyzer
from domain.models.bar import Bar
from domain.models.mtf_state import MTFState
from domain.models.setup_signal import SetupSignal
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

        # Сначала — отбор по тренду и фазе коррекции
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

        return SetupSignal(
            symbol=self.symbol,
            direction=direction,
            confidence=confidence,
            confirmed_timeframes=confirmed,
            rr=avg_rr,
            text=text.strip(),
            timestamp=self.bars_by_tf['15m'][-1].timestamp
        )
