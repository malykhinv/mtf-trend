from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from crypto_screener.domain.models.order_status import OrderStatus


@dataclass
class ProtectiveOrders:
    stop_loss_id: Optional[str] = None
    stop_loss_status: Optional[OrderStatus] = None
    take_profit_id: Optional[str] = None
    take_profit_status: Optional[OrderStatus] = None
    partial_close_id: Optional[str] = None
    partial_close_status: Optional[OrderStatus] = None
    breakeven_id: Optional[str] = None
    breakeven_status: Optional[OrderStatus] = None
