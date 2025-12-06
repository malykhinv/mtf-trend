from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from crypto_screener.domain.models.order_status import OrderStatus


@dataclass
class StopRealignmentResult:
    stop_loss_id: Optional[str] = None
    stop_loss_status: Optional[OrderStatus] = None
    breakeven_id: Optional[str] = None
    breakeven_status: Optional[OrderStatus] = None
