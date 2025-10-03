from enum import Enum


class BalanceSource(str, Enum):
    AVAILABLE_BALANCE = "available_balance"
    WALLET_BALANCE = "wallet_balance"


__all__ = ["BalanceSource"]
