"""Domain model for detected bar anomalies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bar import BarMetrics
from exchange import Exchange
from timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class AnomalyThresholdSnapshot:
    """Snapshot of the thresholds used to determine an anomaly."""

    min_green_move_pct: float
    min_volume_spike: float
    min_relative_volume: float
    min_atr_mult: float


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

