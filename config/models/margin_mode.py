"""Enumeration for margin modes."""
from enum import Enum


class MarginMode(str, Enum):
    """Margin mode options supported by the bot."""

    ISOLATED = "isolated"
    CROSS = "cross"


__all__ = ["MarginMode"]
