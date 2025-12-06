from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.order_status import OrderStatus
from crypto_screener.domain.models.protective_orders import ProtectiveOrders
from crypto_screener.domain.models.setup import Buy
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass
class ActiveTrade:
    symbol: str
    timeframe: Timeframe
    setup: Buy
    detection_time: datetime
    context: Context
    placed_at: datetime
    quantity: float
    status: OrderStatus = OrderStatus.NEW
    entry_average_price: Optional[float] = None
    exit_average_price: Optional[float] = None
    capture_message_id: Optional[str] = None
    postmortem_bars: list[Bar] = field(default_factory=list)
    entry_order_id: Optional[str] = None
    entry_order_status: Optional[OrderStatus] = None
    protective_orders: ProtectiveOrders = field(default_factory=ProtectiveOrders)
    position_id: Optional[str] = None
    remaining_quantity: Optional[float] = None
