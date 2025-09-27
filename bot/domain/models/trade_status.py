"""Lifecycle statuses for a trade."""
from __future__ import annotations

from enum import Enum


class TradeStatus(str, Enum):
    OPENED = "OPENED"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    CANCELLED = "CANCELLED"
