from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import Exchange, Side
from .timezone import ensure_current_timezone


@dataclass(frozen=True, slots=True)
class Wall:
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
