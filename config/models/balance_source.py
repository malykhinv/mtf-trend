"""Enumeration for balance sources."""
from enum import Enum


class BalanceSource(str, Enum):
    """Sources for balance retrieval."""

    AVAILABLE_BALANCE = "available_balance"
    WALLET_BALANCE = "wallet_balance"


__all__ = ["BalanceSource"]
