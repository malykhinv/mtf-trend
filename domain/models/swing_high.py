from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SwingHigh:
    price: float
    timestamp: datetime
    index: int


__all__ = ["SwingHigh"]
