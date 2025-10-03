from enum import Enum


class MarginMode(str, Enum):
    ISOLATED = "isolated"
    CROSS = "cross"


__all__ = ["MarginMode"]
