"""Domain model for detected bar anomalies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.domain.models.bar import BarMetrics
from bot.domain.models.exchange import Exchange
from bot.domain.models.timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class AnomalyThresholdSnapshot:
    """Snapshot of the thresholds used to determine an anomaly."""

    min_green_move_pct: float
    min_volume_spike: float
    min_relative_volume: float
    min_atr_mult: float
    min_upper_wick_pct: float


@dataclass(frozen=True, slots=True)
class Anomaly:
    """Represents an abnormally strong bar worth tracking separately."""

    bar_id: str
    exchange: Exchange
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    metrics: BarMetrics
    thresholds: AnomalyThresholdSnapshot


@dataclass(frozen=True, slots=True)
class AnomalyPerformance:
    """Aggregated performance metrics used to gate future trades."""

    long_profit: float
    long_win_rate: float
    short_profit: float
    short_win_rate: float

    @classmethod
    def permissive(cls) -> "AnomalyPerformance":
        """Return a performance snapshot that never blocks new trades."""

        return cls(long_profit=1.0, long_win_rate=1.0, short_profit=1.0, short_win_rate=1.0)

