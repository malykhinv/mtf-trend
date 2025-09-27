"""Trade domain model used by execution and diary services."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from close_reason import CloseReason
from exchange import Exchange
from signal_direction import SignalDirection
from timeframe import Timeframe
from trade_status import TradeStatus


@dataclass(frozen=True, slots=True)
class Trade:
    trade_id: str
    source_signal_id: str
    exchange: Exchange
    symbol: str
    timeframe: Timeframe
    side: SignalDirection
    timestamp_open: datetime
    entry_price: float
    take_profit_price: float
    stop_loss_price: float
    requested_qty: float
    executed_qty: float
    status: TradeStatus
    timestamp_close: Optional[datetime] = None
    avg_fill_price: Optional[float] = None
    reason_close: Optional[CloseReason] = None
    sl_be_at: Optional[datetime] = None
    order_id: Optional[str] = None
    stop_order_id: Optional[str] = None
    take_order_id: Optional[str] = None
    close_order_id: Optional[str] = None
