"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.value_objects.price import Price
from domain.value_objects.volume import Volume


@dataclass(frozen=True, slots=True)
class Candle:
    timestamp_ms: int
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Volume
    open_interest: Volume

    # region Приватные
    def __post_init__(self) -> None:
        if not isinstance(self.timestamp_ms, int):
            msg = "Candle timestamp_ms must be int unix ms."
            raise ValueError(msg)

        if self.timestamp_ms < 0:
            msg = "Candle timestamp_ms must be >= 0."
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
    # endregion Приватные
