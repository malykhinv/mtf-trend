from __future__ import annotations

from enum import Enum


class OrderRole(str, Enum):
    """Logical role of an order within the breakout strategy."""

    ENTRY = "entry"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT_1 = "take_profit_1"
    TAKE_PROFIT_2 = "take_profit_2"


__all__ = ["OrderRole"]
