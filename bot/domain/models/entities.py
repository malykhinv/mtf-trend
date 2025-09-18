from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from ..enums import BreakDirection, Exchange, Side, Timeframe, TradeStatus


@dataclass(slots=True)
class Candle:
    symbol: str
    exchange: Exchange
    timeframe: Timeframe
    open: float
    high: float
    low: float
    close: float
    volume: float
    started_at: datetime
    closed_at: datetime


@dataclass(slots=True)
class Thresholds:
    atr_multiplier: float = 1.0
    volume_multiplier: float = 1.0
    breakout_threshold: float = 0.0
    tp_multiplier: float = 1.5
    sl_multiplier: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Signal:
    id: str
    candle: Candle
    side: Side
    direction: BreakDirection
    score: float
    triggered_at: datetime
    thresholds: Thresholds
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Trade:
    id: str
    signal_id: str
    exchange: Exchange
    symbol: str
    side: Side
    status: TradeStatus
    entry_price: float
    size: float
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    pnl: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
