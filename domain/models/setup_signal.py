from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from domain.models.confidence import Confidence
from domain.models.side import Side
from domain.models.scenario import Scenario


@dataclass
class SetupSignal:
    symbol: str
    side: Side
    confidence: Confidence
    text: str
    timestamp: datetime
    entry: Optional[float]
    rr: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    scenario: Optional[Scenario] = None

    @property
    def is_order_signal(self) -> bool:
        return self.confidence.is_strong and self.tp is not None and self.sl is not None

    @property
    def is_event_signal(self) -> bool:
        return (self.confidence.is_weak or self.confidence.is_moderate) and self.rr