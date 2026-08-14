"""Frozen contracts for the session-reclaim short study."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd


PROTOCOL_FREEZE_ID = "session_reclaim_short_is_mechanics_20260803_v1"
UNIVERSE_SCHEMA_VERSION = "session_reclaim_universe_v1"
MECHANICS_SCHEMA_VERSION = "session_reclaim_mechanics_v1"
IS_START_MS = int(pd.Timestamp("2025-06-01T00:00:00Z").timestamp() * 1_000)
IS_END_EXCLUSIVE_MS = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1_000)
MINUTE_MS = 60_000
HOUR_MS = 3_600_000


class EntryPolicy(StrEnum):
    MARKET_RECLAIM = "market_reclaim"
    MAKER_REFERENCE_HIGH = "maker_reference_high"
    RETEST_REJECTION = "retest_rejection"


class InvalidationPolicy(StrEnum):
    POKE_TOUCH = "poke_touch"
    UPPER_STRUCTURE = "upper_structure"
    CLOSE1_UPPERCAT = "close1_uppercat"
    CLOSE2_UPPERCAT = "close2_uppercat"


class ProfitPolicy(StrEnum):
    MID_FULL = "mid_full"
    MID50_LOW50 = "mid50_low50"


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    timeframe_minutes: int = 60
    atr_window_bars: int = 24
    pivot_clearance_bars: int = 2
    minimum_swing_atr: float = 2.0
    upper_structure_lookback_hours: int = 24 * 7

    def __post_init__(self) -> None:
        if self.timeframe_minutes != 60:
            raise ValueError("session reclaim v1 is frozen to 60-minute signal bars")
        if self.atr_window_bars < 2:
            raise ValueError("atr_window_bars must be at least two")
        if self.pivot_clearance_bars < 1:
            raise ValueError("pivot_clearance_bars must be positive")
        if self.minimum_swing_atr <= 0:
            raise ValueError("minimum_swing_atr must be positive")
        if self.upper_structure_lookback_hours < 24:
            raise ValueError("upper_structure_lookback_hours must be at least one day")


@dataclass(frozen=True, slots=True)
class MechanicsConfig:
    horizon_minutes: int = 48 * 60
    pending_entry_minutes: int = 6 * 60
    cost_bps: tuple[int, ...] = (0, 2, 4, 6, 10, 20, 30)

    def __post_init__(self) -> None:
        if self.horizon_minutes <= 0 or self.pending_entry_minutes <= 0:
            raise ValueError("mechanics horizons must be positive")
        if not self.cost_bps or self.cost_bps[0] != 0:
            raise ValueError("cost_bps must start with the gross zero-cost case")
        if tuple(sorted(set(self.cost_bps))) != self.cost_bps:
            raise ValueError("cost_bps must be unique and increasing")
