"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.position_side import PositionSide
from domain.models.level import Level


@dataclass(slots=True)
class PendingBreakout:
    breakout_idx: int
    level: Level
    breakout_extreme: float
    side: PositionSide
    level_start_time: int
    level_touch_count: int
    level_min_bars_between_touches: int
    level_max_penetration_atr: float
    level_max_penetration_pct: float
    snapshot_level_high: float
    snapshot_level_low: float
    snapshot_resistance_touch_timestamps_ms: tuple[int, ...]
    snapshot_support_touch_timestamps_ms: tuple[int, ...]
