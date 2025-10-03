"""Enumeration for stop trigger prices."""
from enum import Enum


class StopTrigger(str, Enum):
    """Trigger prices supported for protective orders."""

    MARK_PRICE = "mark_price"
    LAST_PRICE = "last_price"


__all__ = ["StopTrigger"]
