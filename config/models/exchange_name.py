"""Enumeration of supported exchanges."""
from enum import Enum


class ExchangeName(str, Enum):
    """Supported exchange identifiers."""

    BINANCE = "binance"
    BYBIT = "bybit"


__all__ = ["ExchangeName"]
