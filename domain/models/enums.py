"""Enumerations shared across domain models."""

from __future__ import annotations

from enum import Enum


class Exchange(str, Enum):
    """Supported trading venues."""

    BINANCE = "binance"
    BYBIT = "bybit"


class MarginMode(str, Enum):
    """Account margin configuration."""

    ISOLATED = "isolated"
    CROSS = "cross"


class BalanceSource(str, Enum):
    """Different balance sources exposed by exchanges."""

    AVAILABLE = "available_balance"
    WALLET = "wallet_balance"


class StopTrigger(str, Enum):
    """Price reference used for stop orders."""

    MARK = "mark_price"
    LAST = "last_price"


class Side(str, Enum):
    """Order side or book direction."""

    BID = "bid"
    ASK = "ask"


class Signal(str, Enum):
    """Trading signal direction."""

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
