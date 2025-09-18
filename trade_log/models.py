"""Domain models for trade logging."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from domain.models.enums import Side


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """A persisted snapshot of a trade event."""

    timestamp: float
    symbol: str
    side: Side
    outcome: str
    entry: Optional[float]
    take_profit: Optional[float]
    stop_loss: Optional[float]
    quantity: Optional[float]
    fees: Optional[float]
