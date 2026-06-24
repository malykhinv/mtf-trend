from __future__ import annotations

from dataclasses import dataclass

from anomaly_science.contracts.horizons import SUPPORTED_RESEARCH_HORIZONS, is_supported_research_horizon

ATLAS_VERSION = "mvp1_atlas_v4"
ATLAS_DEFAULT_OUTCOME_HORIZONS: tuple[int, ...] = SUPPORTED_RESEARCH_HORIZONS


@dataclass(frozen=True, slots=True)
class AtlasConfig:
    atlas_version: str = ATLAS_VERSION
    outcome_horizon_minutes: int | None = None
    outcome_horizon_minutes_list: tuple[int, ...] = ATLAS_DEFAULT_OUTCOME_HORIZONS
    outcome_continuation_threshold_atr: float = 1.0
    outcome_fade_threshold_atr: float = 1.0
    outcome_chop_threshold_atr: float = 0.35
    min_symbols_for_market_shock_candidate: int = 3

    def __post_init__(self) -> None:
        if not self.atlas_version:
            raise ValueError("atlas_version is required")
        horizons = self._normalize_horizons()
        object.__setattr__(self, "outcome_horizon_minutes_list", horizons)
        if self.outcome_horizon_minutes is not None and self.outcome_horizon_minutes != horizons[0]:
            object.__setattr__(self, "outcome_horizon_minutes", horizons[0])
        if self.outcome_continuation_threshold_atr <= 0:
            raise ValueError("outcome_continuation_threshold_atr must be positive")
        if self.outcome_fade_threshold_atr <= 0:
            raise ValueError("outcome_fade_threshold_atr must be positive")
        if self.outcome_chop_threshold_atr < 0:
            raise ValueError("outcome_chop_threshold_atr must be non-negative")
        if self.min_symbols_for_market_shock_candidate <= 0:
            raise ValueError("min_symbols_for_market_shock_candidate must be positive")

    def _normalize_horizons(self) -> tuple[int, ...]:
        if self.outcome_horizon_minutes is not None:
            horizons = (self.outcome_horizon_minutes,)
        else:
            horizons = tuple(self.outcome_horizon_minutes_list)
        if not horizons:
            raise ValueError("at least one atlas outcome horizon is required")
        deduped: list[int] = []
        for horizon in horizons:
            if not is_supported_research_horizon(horizon):
                allowed = ", ".join(str(value) for value in SUPPORTED_RESEARCH_HORIZONS)
                raise ValueError(f"atlas outcome horizon must be one of: {allowed}")
            if horizon not in deduped:
                deduped.append(horizon)
        return tuple(deduped)
