from __future__ import annotations

from dataclasses import dataclass

from .level_pattern import LevelPattern


@dataclass(frozen=True)
class Level:
    level_low: float
    level_top: float
    width: float
    pattern: LevelPattern


__all__ = ["Level"]
