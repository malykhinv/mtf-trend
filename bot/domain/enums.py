from __future__ import annotations

from enum import Enum, IntEnum


class Exchange(str, Enum):
    BINANCE = "binance"
    BYBIT = "bybit"


class Timeframe(str, Enum):
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class BreakDirection(IntEnum):
    NONE = 0
    LOW_FIRST = -1
    HIGH_FIRST = 1


class TradeStatus(str, Enum):
    OPENED = "opened"
    CLOSED_TP = "closed_tp"
    CLOSED_SL = "closed_sl"
    REJECTED = "rejected"
