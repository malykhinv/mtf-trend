from __future__ import annotations

from dataclasses import dataclass

ATLAS_VERSION = "mvp1_atlas_v1"


@dataclass(frozen=True, slots=True)
class AtlasConfig:
    atlas_version: str = ATLAS_VERSION
    outcome_horizon_minutes: int = 30
    outcome_move_threshold: float = 0.01
    outcome_chop_threshold: float = 0.003
    min_symbols_for_market_shock_candidate: int = 3

    def __post_init__(self) -> None:
        if not self.atlas_version:
            raise ValueError("atlas_version is required")
        if self.outcome_horizon_minutes != 30:
            raise ValueError("MVP1 atlas currently supports only the 30m descriptive outcome horizon")
        if self.outcome_move_threshold <= 0:
            raise ValueError("outcome_move_threshold must be positive")
        if self.outcome_chop_threshold < 0:
            raise ValueError("outcome_chop_threshold must be non-negative")
        if self.min_symbols_for_market_shock_candidate <= 0:
            raise ValueError("min_symbols_for_market_shock_candidate must be positive")
