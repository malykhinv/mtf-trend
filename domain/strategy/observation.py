from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from domain.models import Pressure, Wall
from domain.models.timezone import ensure_current_timezone

from .resync import FeedStatus


@dataclass(frozen=True)
class MarketObservation:
    timestamp: datetime
    symbol: str
    last_price: float
    tick_size: float
    pressure: Optional[Pressure]
    bid_wall: Optional[Wall]
    ask_wall: Optional[Wall]
    bid_opposite_wall_blocks: bool
    ask_opposite_wall_blocks: bool
    available_symbols: Tuple[str, ...]
    feed_status: FeedStatus
    volume_ratio: float
    volume_spike: bool

    def __post_init__(self) -> None:
        ensure_current_timezone(self.timestamp)


__all__ = ["MarketObservation"]
