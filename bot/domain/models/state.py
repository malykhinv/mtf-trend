from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict

DEFAULT_SCOPE = "default"


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


_DEPOSIT_STATES: Dict[str, DepositState] = {}
_USED_STATES: Dict[str, UsedCapitalState] = {}


def _normalize_scope(key: str | None) -> str:
    return key or DEFAULT_SCOPE


def _get_deposit_state(scope: str) -> DepositState:
    state = _DEPOSIT_STATES.get(scope)
    if state is None:
        state = DepositState()
        _DEPOSIT_STATES[scope] = state
    return state


def _get_used_state(scope: str) -> UsedCapitalState:
    state = _USED_STATES.get(scope)
    if state is None:
        state = UsedCapitalState()
        _USED_STATES[scope] = state
    return state


def set_deposit(amount: float, asset: str, updated_at: datetime | None, *, key: str | None = None) -> None:
    """Update the in-memory deposit snapshot for the given key."""

    scope = _normalize_scope(key)
    state = _get_deposit_state(scope)
    state.asset = asset
    state.amount = amount
    state.updated_at = updated_at


def get_deposit_amount(key: str | None = None) -> float:
    scope = _normalize_scope(key)
    return _get_deposit_state(scope).amount


def set_used_amount(amount: float, *, key: str | None = None) -> None:
    scope = _normalize_scope(key)
    state = _get_used_state(scope)
    state.amount = amount


def get_used_amount(key: str | None = None) -> float:
    scope = _normalize_scope(key)
    return _get_used_state(scope).amount
