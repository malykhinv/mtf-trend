from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from domain.models.confidence import Confidence
from domain.models.side import Side
from domain.models.scenario import Scenario
from domain.models.timeframe import Timeframe


@dataclass
class SetupSignal:
    symbol: str
    side: Side
    confidence: Confidence
    confirmed_timeframes: List[Timeframe]
    text: str
    timestamp: datetime
    entry: Optional[float]
    rr: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    scenario: Optional[Scenario] = None

    @property
    def is_order_signal(self) -> bool:
        return self.confidence == Confidence.STRONG and self.tp is not None and self.sl is not None

    @property
    def is_event_signal(self) -> bool:
        return self.confidence in [Confidence.WEAK, Confidence.MODERATE] and self.rr