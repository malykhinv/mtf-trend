from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

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
class ThresholdMetric:
    name: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_abs_value: Optional[float] = None


@dataclass(slots=True)
class Thresholds:
    s: float = 0.0
    t: float = 0.0
    u: float = 0.0
    v: float = 0.0
    w: float = 0.0
    x: float = 0.0
    y: float = 0.0
    allow_long: bool = True
    allow_short: bool = True
    metrics: List[ThresholdMetric] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SignalMetric:
    name: str
    value: float
    passed: bool
    threshold: Optional[ThresholdMetric] = None


@dataclass(slots=True)
class Signal:
    id: str
    candle: Candle
    side: Side
    direction: BreakDirection
    score: float
    triggered_at: datetime
    thresholds: Thresholds
    metrics: List[SignalMetric] = field(default_factory=list)
    allow_long: bool = True
    allow_short: bool = True
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
    used_margin: float = 0.0
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    pnl: Optional[float] = None
    allow_long: bool = True
    allow_short: bool = True
    thresholds_snapshot: Optional[Thresholds] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
