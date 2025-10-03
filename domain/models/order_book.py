from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Tuple

from ._timezone import ensure_current_timezone
from .enums import Exchange


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: float
    quantity: float
    notional: float
    first_seen_at: datetime
    last_update_at: datetime
    min_quantity_seen: float
    max_quantity_seen: float

    def __post_init__(self) -> None:
        ensure_current_timezone(self.first_seen_at, self.last_update_at)


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    exchange: Exchange
    symbol: str
    last_update_id: int
    bids: Tuple[OrderBookLevel, ...]
    asks: Tuple[OrderBookLevel, ...]
    received_at: datetime

    def __post_init__(self) -> None:
        ensure_current_timezone(self.received_at)


@dataclass(frozen=True, slots=True)
class OrderBookUpdate:
    exchange: Exchange
    symbol: str
    first_update_id: int
    last_update_id: int
    bids: Tuple[OrderBookLevel, ...]
    asks: Tuple[OrderBookLevel, ...]
    event_time: datetime

    def __post_init__(self) -> None:
        ensure_current_timezone(self.event_time)


__all__ = [
    "OrderBookLevel",
    "OrderBookSnapshot",
    "OrderBookUpdate",
]
