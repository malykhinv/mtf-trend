from enum import Enum


class StopTrigger(str, Enum):
    MARK_PRICE = "mark_price"
    LAST_PRICE = "last_price"


__all__ = ["StopTrigger"]
