from dataclasses import dataclass
from datetime import datetime

from config.constants import FLOAT_UNDEFINED
from domain.models.confidence import Confidence
from domain.models.side import Side


@dataclass
class SetupSignal:
    symbol: str
    side: Side
    confidence: Confidence
    text: str
    timestamp: datetime
    entry: float = FLOAT_UNDEFINED
    rr: float = FLOAT_UNDEFINED
    sl: float = FLOAT_UNDEFINED
    tp: float = FLOAT_UNDEFINED

    @property
    def is_order_signal(self) -> bool:
        return self.confidence.is_strong and self.tp is not None and self.sl is not None

    @property
    def is_event_signal(self) -> bool:
        return (self.confidence.is_weak or self.confidence.is_moderate) and self.rr