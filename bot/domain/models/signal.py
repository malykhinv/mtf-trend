"""Signal domain models used by the analyzer and orchestrator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .bar import Bar
from .exchange import Exchange
from .signal_direction import SignalDirection
from .timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class ThresholdSnapshot:
    min_green_move_pct: float
    min_volume_spike: float


@dataclass(frozen=True, slots=True)
class SignalLevels:
    entry_price: float
    take_profit_price: float
    stop_loss_price: float


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    bar: Bar
    timestamp: datetime
    exchange: Exchange
    symbol: str
    timeframe: Timeframe
    thresholds: ThresholdSnapshot
    direction: SignalDirection
    levels: SignalLevels
