"""Configuration model for wall settings."""
from dataclasses import dataclass

from .wall_absolute_thresholds import WallAbsoluteThresholds


@dataclass(frozen=True)
class WallSettings:
    absolute: WallAbsoluteThresholds
    relative_multiplier: float
    persist_s_top: float
    persist_s_alt: float
    persist_s_listing: float


__all__ = ["WallSettings"]
