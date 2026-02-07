"""Source-of-truth breakout parameter ranges for grid backtests."""

from __future__ import annotations

from dataclasses import dataclass
from math import prod

from domain.enums.sl_mode import SLMode

TARGET_PARAMETER_COMBINATIONS = 5832


@dataclass(frozen=True, slots=True)
class BreakoutParams:
    lookback: int
    volume_mult: float
    retest_window: int
    retest_zone: float
    min_rr: float
    sl_mode: SLMode
    tp2_mult: float
    symbol: str


BREAKOUT_PARAMETER_GRID: dict[str, list[float | int | SLMode]] = {
    "lookback": [8, 13, 21, 34, 55, 89],
    "volume_mult": [1.1, 1.3, 1.5],
    "retest_window": [2, 4, 6],
    "retest_zone": [0.0015, 0.0020, 0.0030],
    "min_rr": [1.0, 1.5, 2.0],
    "sl_mode": [SLMode.LEVEL, SLMode.BREAKOUT_EXTREME],
    "tp2_mult": [1.1, 1.25, 1.4, 1.5, 1.75, 2.0],
}

PARAMETER_GRID_SIZE = prod(len(values) for values in BREAKOUT_PARAMETER_GRID.values())
