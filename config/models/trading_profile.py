from enum import Enum


class TradingProfile(str, Enum):
    AUTO = "auto"
    TOP = "T"
    ALT = "A"
    LISTING = "L"


__all__ = ["TradingProfile"]
