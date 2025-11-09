from __future__ import annotations

from enum import Enum


class LevelPattern(str, Enum):
    MULTIPLE_SWINGS = "multiple_swings"
    SINGLE_WITH_CONSOLIDATION = "single_with_consolidation"


__all__ = ["LevelPattern"]
