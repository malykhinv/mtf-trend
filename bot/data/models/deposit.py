from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(slots=True)
class DepositSnapshot:
    asset: str
    balance: float
    raw: Mapping[str, Any] | None = None
