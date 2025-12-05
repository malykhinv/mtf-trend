from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from crypto_screener.domain.models.order_side import OrderSide
from crypto_screener.domain.models.order_status import OrderStatus


@dataclass(frozen=True)
class OrderInfo:
    id: str
    symbol: str
    side: OrderSide
    quantity: float
    filled: float
    status: OrderStatus
    average_price: Optional[float] = None
