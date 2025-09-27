"""Account and balance related interfaces."""
from __future__ import annotations

from typing import Protocol


class BalanceProvider(Protocol):
    def current_deposit(self) -> float:  # pragma: no cover - interface definition
        ...
