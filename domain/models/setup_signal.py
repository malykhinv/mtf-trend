from dataclasses import dataclass
from datetime import datetime
from typing import Literal, List, Optional


@dataclass
class SetupSignal:
    symbol: str
    direction: Literal['long', 'short']
    confidence: Literal['low', 'medium', 'high']
    confirmed_timeframes: List[str]
    rr: float
    text: str
    timestamp: datetime
    entry: float
    sl: float
    tp: float
    scenario: Optional[Literal['rebound', 'false_breakout', 'breakout', 'momentum']] = None
