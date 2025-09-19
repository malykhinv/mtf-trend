from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ..enums import BreakDirection, Exchange, Side, Timeframe, TradeStatus
from .metadata import SignalMetadata, TradeMetadata


if TYPE_CHECKING:
    from ..services.metrics_service import SelectionMetricsSnapshot


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
    id: Optional[str] = None
    quote_volume: Optional[float] = None


@dataclass(slots=True)
class ThresholdMetric:
    name: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_abs_value: Optional[float] = None


@dataclass(slots=True)
class Thresholds:
    id: Optional[str] = None
    min_relative_volume: float = 0.0
    max_relative_volume: float = 0.0
    min_atr_mult: float = 0.0
    min_pct_move: float = 0.0
    max_pct_move: float = 0.0
    max_upper_wick_pct: float = 0.0
    max_lower_wick_pct: float = 0.0
    short_pct_move_ranges: List[tuple[Optional[float], Optional[float]]] = field(
        default_factory=list
    )
    short_relative_volume_ranges: List[tuple[Optional[float], Optional[float]]] = field(
        default_factory=list
    )
    allow_long: bool = True
    allow_short: bool = True
    metrics: List[ThresholdMetric] = field(default_factory=list)
    metadata: SignalMetadata | None = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


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
    candle_id: Optional[str] = None
    timeframe: Optional[Timeframe] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    metrics: List[SignalMetric] = field(default_factory=list)
    allow_long: bool = True
    allow_short: bool = True
    metrics_snapshot: SelectionMetricsSnapshot | None = None
    metadata: SignalMetadata | None = None


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
    source_signal_id: Optional[str] = None
    timeframe: Optional[Timeframe] = None
    used_margin: float = 0.0
    exit_price: Optional[float] = None
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    tp_pct: Optional[float] = None
    sl_pct: Optional[float] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    allow_long: bool = True
    allow_short: bool = True
    thresholds_snapshot: Optional[Thresholds] = None
    metadata: TradeMetadata | None = None
