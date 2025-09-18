from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class DepositState:
    """In-memory snapshot of the trading capital state."""

    asset: str = "USDT"
    amount: float = 0.0
    updated_at: datetime | None = None


@dataclass(slots=True)
class UsedCapitalState:
    """In-memory snapshot of the used part of the trading capital."""

    amount: float = 0.0


DEPOSIT_USDT = DepositState()
USED_CAPITAL = UsedCapitalState()


def set_deposit(amount: float, asset: str, updated_at: datetime | None) -> None:
    """Update the in-memory deposit snapshot."""

    DEPOSIT_USDT.asset = asset
    DEPOSIT_USDT.amount = amount
    DEPOSIT_USDT.updated_at = updated_at


def get_deposit_amount() -> float:
    return DEPOSIT_USDT.amount


def set_used_amount(amount: float) -> None:
    USED_CAPITAL.amount = amount


def get_used_amount() -> float:
    return USED_CAPITAL.amount
