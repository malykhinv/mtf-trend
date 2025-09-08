from dataclasses import dataclass

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
