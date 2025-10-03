"""Strategy state definitions."""

from __future__ import annotations

from enum import Enum


class StrategyState(str, Enum):
    """Finite state machine states for the trading strategy."""

    SCANNING = "scanning"
    FOCUSED = "focused"
    IN_POSITION = "in_position"
    RESYNC = "resync"


__all__ = ["StrategyState"]
