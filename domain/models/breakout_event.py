"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.models.level import Level
from domain.value_objects.price import Price
from domain.value_objects.volume import Volume


@dataclass(frozen=True, slots=True)
class BreakoutEvent:
    level: Level
    breakout_time: datetime
    breakout_price: Price
    volume_before: Volume
    volume_after: Volume
    oi_value: Volume

    # region Приватные
    def __post_init__(self) -> None:
        if self.breakout_time is None:
            msg = "Breakout time is required."
            raise ValueError(msg)

        if self.volume_after.value == 0:
            msg = "Breakout volume_after must be positive."
            raise ValueError(msg)
    # endregion Приватные
