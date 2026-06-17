from __future__ import annotations

from dataclasses import dataclass

ATLAS_VERSION = "mvp1_atlas_v2"


@dataclass(frozen=True, slots=True)
class AtlasConfig:
    atlas_version: str = ATLAS_VERSION
    outcome_horizon_minutes: int = 30
    outcome_continuation_threshold_atr: float = 1.0
    outcome_fade_threshold_atr: float = 1.0
    outcome_chop_threshold_atr: float = 0.35
    min_symbols_for_market_shock_candidate: int = 3

    def __post_init__(self) -> None:
        if not self.atlas_version:
            raise ValueError("atlas_version is required")
        if self.outcome_horizon_minutes != 30:
            raise ValueError("MVP1 atlas currently supports only the 30m descriptive outcome horizon")
        if self.outcome_continuation_threshold_atr <= 0:
            raise ValueError("outcome_continuation_threshold_atr must be positive")
        if self.outcome_fade_threshold_atr <= 0:
            raise ValueError("outcome_fade_threshold_atr must be positive")
        if self.outcome_chop_threshold_atr < 0:
            raise ValueError("outcome_chop_threshold_atr must be non-negative")
        if self.min_symbols_for_market_shock_candidate <= 0:
            raise ValueError("min_symbols_for_market_shock_candidate must be positive")
