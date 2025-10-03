"""Configuration model for wall shift settings."""
from dataclasses import dataclass


@dataclass(frozen=True)
class WallShiftSettings:
    shift_min_ticks: int
    shift_hold_s: float
    vanish_drop: float
    vanish_grace_ms: int


__all__ = ["WallShiftSettings"]
