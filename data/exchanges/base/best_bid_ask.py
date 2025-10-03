"""Best bid/ask quote snapshot emitted by exchanges."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class BestBidAsk:
    """Best bid/ask quote snapshot emitted by exchanges."""

    exchange: str
    symbol: str
    bid_price: float
    bid_quantity: float
    ask_price: float
    ask_quantity: float
    event_time: datetime


__all__ = ["BestBidAsk"]
