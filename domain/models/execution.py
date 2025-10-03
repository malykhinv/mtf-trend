from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._timezone import ensure_current_timezone
from .enums import Exchange, Side


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    exchange: Exchange
    symbol: str
    order_id: str
    side: Side
    price: float
    quantity: float
    executed_qty: float
    status: str
    commission: float
    executed_at: datetime

    def __post_init__(self) -> None:
        ensure_current_timezone(self.executed_at)


__all__ = ["ExecutionReport"]
