"""Models describing trade executions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._timezone import ensure_current_timezone
from .enums import Exchange, Side


@dataclass(frozen=True, slots=True)
class Trade:
    """Aggregated trade information received from the exchange."""

    trade_id: str
    exchange: Exchange
    symbol: str
    executed_at: datetime
    price: float
    quantity: float
    side: Side

    def __post_init__(self) -> None:
        ensure_current_timezone(self.executed_at)


__all__ = ["Trade"]
