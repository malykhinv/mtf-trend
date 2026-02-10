"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from domain.enums.position_side import PositionSide
from domain.models.level import Level


@dataclass(slots=True)
class PendingBreakout:
    breakout_idx: int
    level: Level
    breakout_extreme: float
    side: PositionSide
    level_start_time: pd.Timestamp
