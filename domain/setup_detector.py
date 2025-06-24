from domain.mtf_analyzer import MTFAnalyzer
from domain.models.bar import Bar
from domain.models.mtf_state import MTFState
from domain.models.setup_signal import SetupSignal
from typing import Dict, List, Literal


class SetupDetector:
    def __init__(self, symbol: str, bars_by_tf: Dict[str, List[Bar]], atr_by_tf: Dict[str, float]):
        self.symbol = symbol
        self.bars_by_tf = bars_by_tf
        self.atr_by_tf = atr_by_tf
        self.timeframes = ["1d", "4h", "1h", "15m"]

    def detect(self) -> SetupSignal | None:
        mtf_states: Dict[str, MTFState] = {}

        for tf in self.timeframes:
            analyzer = MTFAnalyzer(bars=self.bars_by_tf[tf], timeframe=tf, atr=self.atr_by_tf[tf])
            mtf_states[tf] = analyzer.analyze()

        confirmed = [tf for tf in self.timeframes
                     if mtf_states[tf].trend in ["up", "down"]
                     and not mtf_states[tf].is_in_correction]

        if len(confirmed) < 2:
            return None

        base_trend = mtf_states[confirmed[0]].trend
        if not all(mtf_states[tf].trend == base_trend for tf in confirmed):
            return None

        direction: Literal['long', 'short'] = "long" if base_trend == "up" else "short"

        rr_values = [mtf_states[tf].rr_potential for tf in confirmed]
        avg_rr = round(sum(rr_values) / len(rr_values), 2)

        if avg_rr < 2.0:
            return None

        confidence: Literal['low', 'medium', 'high'] = "low"
        if len(confirmed) == 3:
            confidence = "medium"
        elif len(confirmed) == 4:
            confidence = "high"

        text = f"{self.symbol}: {direction.upper()} setup\n"
        for tf in confirmed:
            text += f"{tf.upper()} trend confirmed, RR={mtf_states[tf].rr_potential}\n"

        return SetupSignal(
            symbol=self.symbol,
            direction=direction,
            confidence=confidence,
            confirmed_timeframes=confirmed,
            rr=avg_rr,
            text=text.strip(),
            timestamp=self.bars_by_tf['15m'][-1].timestamp
        )
