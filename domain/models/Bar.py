from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    atr: Optional[float] = None
