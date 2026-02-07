"""Supported derivatives exchanges."""

from enum import Enum


class Exchange(str, Enum):
    BINANCE = "BINANCE"
    BYBIT = "BYBIT"
    OKX = "OKX"
