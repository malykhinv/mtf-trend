from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .band import Band
from .swing_high import SwingHigh


@dataclass(frozen=True)
class SwingsOutput:
    swings: list[SwingHigh]
    has_consolidation: bool
    consolidation_band: Optional[Band]


__all__ = ["SwingsOutput"]
