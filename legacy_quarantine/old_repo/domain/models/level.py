"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.level_type import LevelType
from domain.value_objects.price import Price


@dataclass(frozen=True, slots=True)
class Level:
    price: Price
    level_type: LevelType
    formation_timestamp_ms: int
    lookback: int
    shadow_ratio: float
    volume_before: float | None = None
    volume_after: float | None = None
    touch_count: int = 0
    reaction_strength: float = 0.0
    level_score: float = 0.0

    # region Приватные
    def __post_init__(self) -> None:
        if not isinstance(self.formation_timestamp_ms, int):
            msg = "Level formation_timestamp_ms must be int unix ms."
            raise ValueError(msg)

        if self.formation_timestamp_ms < 0:
            msg = "Level formation_timestamp_ms must be >= 0."
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

        if self.touch_count < 0:
            msg = "Level touch_count cannot be negative."
            raise ValueError(msg)

        if self.reaction_strength < 0:
            msg = "Level reaction_strength cannot be negative."
            raise ValueError(msg)

        if self.level_score < 0:
            msg = "Level level_score cannot be negative."
            raise ValueError(msg)
    # endregion Приватные
