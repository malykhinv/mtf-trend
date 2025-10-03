from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class BestBidAsk:
    exchange: str
    symbol: str
    bid_price: float
    bid_quantity: float
    ask_price: float
    ask_quantity: float
    event_time: datetime


__all__ = ["BestBidAsk"]
