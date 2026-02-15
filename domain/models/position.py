"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.value_objects.price import Price
from domain.value_objects.volume import Volume


@dataclass(slots=True)
class Position:
    entry_price: Price
    entry_timestamp_ms: int
    size: Volume
    stop_loss: Price
    take_profit_1: Price
    take_profit_2: Price
    tp1_done: bool = False
    sl_moved_to_be: bool = False

    # region Приватные
    def __post_init__(self) -> None:
        if self.entry_timestamp_ms is None:
            msg = "Position entry_timestamp_ms is required."
            raise ValueError(msg)

        if self.size.value <= 0:
            msg = "Position size must be positive."
            raise ValueError(msg)
    # endregion Приватные
