from dataclasses import dataclass

from typing import Any

from .enums import Side, OrderType


@dataclass(frozen=True)
class OrderSpec:
    symbol: str
    side: Side
    type: OrderType
    quantity: float
    price: float | None = None


@dataclass(frozen=True)
class PositionPlan:
    symbol: str
    entry_price: float
    stop_loss: float
    take_profit1: float
    take_profit2: float
    trail_start: float
    trail_distance: float
    quantity: float
    tp1_qty: float
    tp2_qty: float
    tail_qty: float
    window_high: float


@dataclass(frozen=True)
class OrderExecution:
    """Execution details returned from a :class:`Trader` implementation."""

    symbol: str
    order_id: int | None = None
    status: str | None = None
    filled_quantity: float | None = None
    price: float | None = None
    average_price: float | None = None
    raw: dict[str, Any] | None = None

    @property
    def execution_price(self) -> float | None:
        """Best available execution price derived from exchange response."""

        if self.average_price is not None and self.average_price > 0.0:
            return self.average_price
        if self.price is not None and self.price > 0.0:
            return self.price
        return None
