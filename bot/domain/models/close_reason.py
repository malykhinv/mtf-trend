"""Reasons that can close a trade."""
from __future__ import annotations

from enum import Enum


class CloseReason(str, Enum):
    TAKE_PROFIT = "tp"
    STOP_LOSS = "sl"
    AGGRESSION = "aggression"
    MANUAL = "manual"
