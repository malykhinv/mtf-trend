from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .timezone import ensure_current_timezone


@dataclass(frozen=True, slots=True)
class Pressure:
    computed_at: datetime
    buy_pressure: float
    sell_pressure: float
    imbalance_ratio: float

    def __post_init__(self) -> None:
        ensure_current_timezone(self.computed_at)


__all__ = ["Pressure"]
