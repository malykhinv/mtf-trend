from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from crypto_screener.domain.models.order_info import OrderInfo
from crypto_screener.domain.models.order_status import OrderStatus


@dataclass
class ProtectiveOrderStatuses:
    take_profit: Optional[OrderInfo] = None
    take_profit_status: Optional[OrderStatus] = None
    stop_loss: Optional[OrderInfo] = None
    stop_loss_status: Optional[OrderStatus] = None
    breakeven: Optional[OrderInfo] = None
    breakeven_status: Optional[OrderStatus] = None
    partial_close: Optional[OrderInfo] = None
    partial_close_status: Optional[OrderStatus] = None
