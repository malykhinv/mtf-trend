"""Models describing candlestick data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._timezone import ensure_belgrade_timezone


@dataclass(frozen=True, slots=True)
class Candle:
    """Single OHLCV candle aggregated over a fixed interval."""

    open_time: datetime
    close_time: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    quote_volume: float

    def __post_init__(self) -> None:
        ensure_belgrade_timezone(self.open_time, self.close_time)


__all__ = ["Candle"]
