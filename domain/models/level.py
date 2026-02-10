"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.enums.level_type import LevelType
from domain.value_objects.price import Price


@dataclass(frozen=True, slots=True)
class Level:
    price: Price
    level_type: LevelType
    formation_time: datetime
    lookback: int
    shadow_ratio: float
    formation_timestamp: datetime | None = None
    volume_before: float | None = None
    volume_after: float | None = None

    # область Приватные
    def __post_init__(self) -> None:
        if self.formation_time is None:
            msg = "Level formation_time is required."
            raise ValueError(msg)

        if self.formation_timestamp is None:
            object.__setattr__(self, "formation_timestamp", self.formation_time)
        elif not isinstance(self.formation_timestamp, datetime):
            msg = "Level formation_timestamp must be datetime."
            raise ValueError(msg)

        if self.lookback <= 0:
            msg = "Level lookback must be positive."
            raise ValueError(msg)

        if self.shadow_ratio < 0:
            msg = "Level shadow_ratio cannot be negative."
            raise ValueError(msg)

        if self.volume_before is not None and self.volume_before < 0:
            msg = "Level volume_before cannot be negative."
            raise ValueError(msg)

        if self.volume_after is not None and self.volume_after < 0:
            msg = "Level volume_after cannot be negative."
            raise ValueError(msg)
    # конец области Приватные
