from __future__ import annotations

from enum import Enum


class Exchange(str, Enum):
    BINANCE_FUTURES = "binance_futures"
    BYBIT_PERPETUAL = "bybit_perpetual"


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class BreakDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class TradeStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"
