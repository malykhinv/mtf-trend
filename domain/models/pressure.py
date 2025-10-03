"""Models capturing order book pressure metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._timezone import ensure_belgrade_timezone


@dataclass(frozen=True, slots=True)
class Pressure:
    """Pressure metrics derived from the order book."""

    computed_at: datetime
    buy_pressure: float
    sell_pressure: float
    imbalance_ratio: float

    def __post_init__(self) -> None:
        ensure_belgrade_timezone(self.computed_at)


__all__ = ["Pressure"]
