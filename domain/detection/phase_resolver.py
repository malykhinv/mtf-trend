from typing import Dict

from domain.models.mtf_profile import MTFProfile
from domain.models.mtf_state import MTFState
from domain.models.timeframe import Timeframe
from domain.mtf_analyzer import MTFAnalyzer


class PhaseResolver:
    def __init__(self, tfs: MTFProfile, bars_by_tf: Dict[Timeframe, list], atr_by_tf: Dict[Timeframe, float]):
        self.tfs = tfs
        self.bars_by_tf = bars_by_tf
        self.atr_by_tf = atr_by_tf

    def resolve(self) -> Dict[Timeframe, MTFState]:
        mtf_states = {}

        for tf in self.tfs:
            bars = self.bars_by_tf[tf]
            atr = self.atr_by_tf[tf]
            analyzer = MTFAnalyzer(bars, tf, atr)
            mtf_states[tf] = analyzer.analyze()

        return mtf_states
