# domain/detection/setup_detector.py

from typing import Optional, List, Callable, Dict

from domain.detection.pump.pump_setup import PumpSetup
from domain.models.bar import Bar
from domain.models.mtf_profile import MTFProfile
from domain.models.setup_signal import SetupSignal
from domain.models.timeframe import Timeframe


class SetupDetector:

    @staticmethod
    def detect(
            symbol: str,
            tfs: MTFProfile,
            bars_by_tf: Dict[Timeframe, List[Bar]],
    ) -> Optional[SetupSignal]:
        setup_classes: List[Callable] = [PumpSetup]

        for setup_cls in setup_classes:
            setup = setup_cls(symbol=symbol, tfs=tfs, bars_by_tf=bars_by_tf)
            signal = setup.validated_or_none()
            if signal:
                return signal

        return None
