from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    entry_price: float
    pnl: float
    leverage: Optional[float] = None
