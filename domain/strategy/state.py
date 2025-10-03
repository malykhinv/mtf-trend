from __future__ import annotations

from enum import Enum


class StrategyState(str, Enum):
    SCANNING = "scanning"
    FOCUSED = "focused"
    IN_POSITION = "in_position"
    RESYNC = "resync"


__all__ = ["StrategyState"]
