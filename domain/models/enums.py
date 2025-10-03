from __future__ import annotations
from enum import Enum


class Exchange(str, Enum):
    BINANCE = "binance"
    BYBIT = "bybit"


class MarginMode(str, Enum):
    ISOLATED = "isolated"
    CROSS = "cross"


class BalanceSource(str, Enum):
    AVAILABLE = "available_balance"
    WALLET = "wallet_balance"


class StopTrigger(str, Enum):
    MARK = "mark_price"
    LAST = "last_price"


class Side(str, Enum):
    BID = "bid"
    ASK = "ask"


class Signal(str, Enum):
    NONE = "none"
    LONG = "long"
    SHORT = "short"


__all__ = [
    "Exchange",
    "MarginMode",
    "BalanceSource",
    "StopTrigger",
    "Side",
    "Signal",
]
