from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import Exchange, Side
from .timezone import ensure_current_timezone


@dataclass(frozen=True, slots=True)
class Trade:
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
