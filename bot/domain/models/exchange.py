"""Exchange enumeration."""
from __future__ import annotations

from enum import Enum


class Exchange(str, Enum):
    BINANCE = "binance"
    BYBIT = "bybit"
