"""Support/resistance level model."""

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

    def __post_init__(self) -> None:
        if self.formation_time is None:
            msg = "Level formation_time is required."
            raise ValueError(msg)

        if self.lookback <= 0:
            msg = "Level lookback must be positive."
            raise ValueError(msg)

        if self.shadow_ratio < 0:
            msg = "Level shadow_ratio cannot be negative."
            raise ValueError(msg)
