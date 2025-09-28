"""Runtime configuration value objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from bot.domain.models.timeframe import Timeframe


class RuntimeMode(str, Enum):
    """Supported runtime modes for the trading bot."""

    LIVE = "live"
    BACKTEST = "backtest"


@dataclass(frozen=True, slots=True)
class BacktestSettings:
    """Runtime settings required to launch a historical backtest."""

    symbol: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    limit: int | None = None
