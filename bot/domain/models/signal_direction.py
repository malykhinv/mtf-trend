"""Direction of a trading signal."""
from __future__ import annotations

from enum import Enum


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def is_long(self) -> bool:
        return self is SignalDirection.LONG

    @property
    def is_short(self) -> bool:
        return self is SignalDirection.SHORT
