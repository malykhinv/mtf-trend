from __future__ import annotations

from datetime import datetime
from typing import Protocol


class OhlcvSnapshot(Protocol):
    """Protocol representing a validated OHLCV snapshot."""

    opened_at: datetime
    closed_at: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    quote_volume: float | None
