from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Band:
    low: float
    high: float
    start_timestamp: datetime
    end_timestamp: datetime


__all__ = ["Band"]
