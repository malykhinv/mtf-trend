from typing import Optional, List, Callable

from domain.detection.momentum.impl.momentum_initiation import MomentumInitiation
from domain.detection.momentum.impl.momentum_pullback import MomentumPullback
from domain.models.setup_signal import SetupSignal
from domain.models.confidence import Confidence
from domain.detection.flat.impl.flat_bounce import FlatBounce
from domain.detection.flat.impl.flat_fake_breakout import FlatFakeBreakout


class SetupDetector:
    def __init__(self, symbol, tfs, bars_by_tf, atr_by_tf, swings, mtf_states, confidence: Confidence):
        self.symbol = symbol
        self.tfs = tfs
        self.bars_by_tf = bars_by_tf
        self.atr_by_tf = atr_by_tf
        self.swings = swings
        self.mtf_states = mtf_states
        self.confidence = confidence

    def detect(self) -> Optional[SetupSignal]:
        setup_classes: List[Callable] = [
            FlatFakeBreakout,
            FlatBounce,
            MomentumInitiation,
            MomentumPullback
        ]

        for setup_cls in setup_classes:
            setup = setup_cls(
                symbol=self.symbol,
                tfs=self.tfs,
                bars_by_tf=self.bars_by_tf,
                atr_by_tf=self.atr_by_tf,
                swings=self.swings,
                confidence=self.confidence,
                mtf_states=self.mtf_states
            )
            signal = setup.detect()
            if signal:
                return signal

        return None
