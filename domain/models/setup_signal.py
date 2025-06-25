from dataclasses import dataclass
from datetime import datetime
from typing import Literal, List, Optional


@dataclass
class SetupSignal:
    symbol: str
    direction: Literal['long', 'short']
    confidence: Literal['low', 'medium', 'high']
    confirmed_timeframes: List[str]
    text: str
    timestamp: datetime
    entry: Optional[float]
    rr: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    scenario: Optional[Literal['rebound', 'false_breakout', 'breakout', 'momentum']] = None

    @property
    def is_order_signal(self) -> bool:
        return self.confidence == 'high' and self.tp is not None and self.sl is not None

    @property
    def is_event_signal(self) -> bool:
        return self.confidence in ['low', 'medium'] and self.rr