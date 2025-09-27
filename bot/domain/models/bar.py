"""Domain model describing a single closed bar with derived metrics."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .exchange import Exchange
from .timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class BarMetrics:
    pct_move: float
    relative_volume: float
    atr_mult: float
    upper_wick_pct: float
    body_pct: float
    lower_wick_pct: float
    pct_to_low_break: float
    pct_to_high_break: float
    break_direction: int


@dataclass(frozen=True, slots=True)
class Bar:
    bar_id: str
    exchange: Exchange
    symbol: str
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    metrics: BarMetrics
