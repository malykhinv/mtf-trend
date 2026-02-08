"""Source-of-truth breakout parameter ranges for grid backtests."""

from __future__ import annotations

from dataclasses import dataclass
from math import prod

from constants import (
    BREAKOUT_LOOKBACK_VALUES,
    BREAKOUT_MIN_RR_VALUES,
    BREAKOUT_RETEST_WINDOW_VALUES,
    BREAKOUT_RETEST_ZONE_VALUES,
    BREAKOUT_SL_MODE_VALUES,
    BREAKOUT_TARGET_PARAMETER_COMBINATIONS,
    BREAKOUT_TP2_MULT_VALUES,
    BREAKOUT_VOLUME_MULT_VALUES,
)
from domain.enums.sl_mode import SLMode
from domain.enums.timeframe import Timeframe

TARGET_PARAMETER_COMBINATIONS = BREAKOUT_TARGET_PARAMETER_COMBINATIONS


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
    levels_timeframe: Timeframe = Timeframe.D1
    entry_timeframe: Timeframe = Timeframe.M15


BREAKOUT_PARAMETER_GRID: dict[str, list[float | int | SLMode]] = {
    "lookback": list(BREAKOUT_LOOKBACK_VALUES),
    "volume_mult": list(BREAKOUT_VOLUME_MULT_VALUES),
    "retest_window": list(BREAKOUT_RETEST_WINDOW_VALUES),
    "retest_zone": list(BREAKOUT_RETEST_ZONE_VALUES),
    "min_rr": list(BREAKOUT_MIN_RR_VALUES),
    "sl_mode": list(BREAKOUT_SL_MODE_VALUES),
    "tp2_mult": list(BREAKOUT_TP2_MULT_VALUES),
}

PARAMETER_GRID_SIZE = prod(len(values) for values in BREAKOUT_PARAMETER_GRID.values())
