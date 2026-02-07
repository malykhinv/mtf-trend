"""OHLCV candle model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.value_objects.price import Price
from domain.value_objects.volume import Volume


@dataclass(frozen=True, slots=True)
class Candle:
    timestamp: datetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Volume
    open_interest: Volume

    def __post_init__(self) -> None:
        if self.timestamp is None:
            msg = "Candle timestamp is required."
            raise ValueError(msg)

        if self.high.value < self.low.value:
            msg = "Candle high cannot be lower than low."
            raise ValueError(msg)

        if not (self.low.value <= self.open.value <= self.high.value):
            msg = "Candle open price must be inside [low, high]."
            raise ValueError(msg)

        if not (self.low.value <= self.close.value <= self.high.value):
            msg = "Candle close price must be inside [low, high]."
            raise ValueError(msg)
