# domain/detection/setup_detector.py

from typing import Optional, List, Dict
from datetime import datetime

from domain.detection.pump.pump_setup import PumpSetup
from domain.models.bar import Bar
from domain.models.mtf_profile import MTFProfile
from domain.models.setup_signal import SetupSignal
from domain.models.timeframe import Timeframe


class SetupDetector:
    """
    Класс-детектор. Автоматически перебирает возможные типы сетапов, используя переданные данные и параметры.
    """
    @staticmethod
    def detect(
            symbol: str,
            tfs: MTFProfile,
            bars_by_tf: Dict[Timeframe, List[Bar]],
            pump_start_time: Optional[datetime] = None,
    ) -> Optional[SetupSignal]:
        """Перебирает возможные сетапы и возвращает первый подходящий сигнал."""
        setup_classes = [PumpSetup]
        for setup_cls in setup_classes:
            setup = setup_cls(symbol=symbol, tfs=tfs, bars_by_tf=bars_by_tf,
                              pump_start_time=pump_start_time)
            signal = setup.validated_or_none()
            if signal:
                return signal

        return None
