from enum import Enum


class ExchangeName(str, Enum):
    BINANCE = "binance"
    BYBIT = "bybit"


__all__ = ["ExchangeName"]
