"""Models related to order execution reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._timezone import ensure_belgrade_timezone
from .enums import Exchange, Side


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Execution result for a placed order."""

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
        ensure_belgrade_timezone(self.executed_at)


__all__ = ["ExecutionReport"]
