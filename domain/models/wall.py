"""Models describing large order book walls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._timezone import ensure_current_timezone
from .enums import Exchange, Side


@dataclass(frozen=True, slots=True)
class Wall:
    """Order book wall representation tracked by the strategy."""

    exchange: Exchange
    symbol: str
    side: Side
    price: float
    quantity: float
    notional: float
    first_seen_at: datetime
    last_seen_at: datetime

    def __post_init__(self) -> None:
        ensure_current_timezone(self.first_seen_at, self.last_seen_at)


__all__ = ["Wall"]
