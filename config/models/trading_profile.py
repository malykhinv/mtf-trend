"""Enumeration of trading profiles."""
from enum import Enum


class TradingProfile(str, Enum):
    """Available trading profiles."""

    AUTO = "auto"
    TOP = "T"
    ALT = "A"
    LISTING = "L"


__all__ = ["TradingProfile"]
