from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Bar:
    """Свеча OHLC."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

