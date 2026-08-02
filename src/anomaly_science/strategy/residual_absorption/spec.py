"""Pre-registered research contract for residual response plus OI absorption."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from anomaly_science.market_context.sessions import UTC_SESSION_CALENDAR_VERSION
from anomaly_science.universe.session_liquidity import SessionLiquidityUniverseConfig
from anomaly_science.validation.research_split import CalendarResearchSplit

RESIDUAL_ABSORPTION_RESEARCH_SPLIT = CalendarResearchSplit(
    split_version="residual_absorption_calendar_split_v1",
    is_start=date(2025, 6, 3),
    oos_start=date(2026, 1, 1),
    oos_end_exclusive=date(2026, 6, 18),
)


@dataclass(frozen=True, slots=True)
class StructuralDistanceFloorSpec:
    """Admissibility floor; it never manufactures or moves a physical TP/SL."""

    policy_version: str = "structural_distance_floor_v1"
    same_type_history_sessions: int = 20
    primary_noise_quantile: float = 0.90
    sensitivity_noise_quantiles: tuple[float, ...] = (0.75, 0.95)
    target_cost_statistic: str = "roundtrip_execution_cost_p95"
    action_when_anchor_inside_floor: str = "reject_trade"

    def __post_init__(self) -> None:
        if self.same_type_history_sessions <= 0:
            raise ValueError("same_type_history_sessions must be positive")
        quantiles = (self.primary_noise_quantile, *self.sensitivity_noise_quantiles)
        if any(not 0.0 < value < 1.0 for value in quantiles):
            raise ValueError("noise quantiles must be in (0, 1)")
        if self.action_when_anchor_inside_floor != "reject_trade":
            raise ValueError("structural anchors inside the floor must reject the trade")


@dataclass(frozen=True, slots=True)
class ResidualAbsorptionResearchSpec:
    protocol_version: str = "residual_absorption_protocol_v1"
    feature_schema_version: str = "residual_absorption_features_v1"
    event_schema_version: str = "residual_absorption_event_v1"
    snapshot_interval_minutes: int = 5
    session_calendar_version: str = UTC_SESSION_CALENDAR_VERSION
    research_split: CalendarResearchSplit = RESIDUAL_ABSORPTION_RESEARCH_SPLIT
    universe: SessionLiquidityUniverseConfig = field(
        default_factory=SessionLiquidityUniverseConfig
    )
    high_resolution_request_version: str = "event_scoped_high_resolution_v1"
    allowed_high_resolution_sources: tuple[str, ...] = ("candles_1s", "aggtrades")
    distance_floor: StructuralDistanceFloorSpec = field(
        default_factory=StructuralDistanceFloorSpec
    )

    def __post_init__(self) -> None:
        if self.snapshot_interval_minutes != 5:
            raise ValueError("v1 snapshots are fixed at five-minute boundaries")
        if not self.allowed_high_resolution_sources:
            raise ValueError("at least one high-resolution source must be declared")


__all__ = [
    "RESIDUAL_ABSORPTION_RESEARCH_SPLIT",
    "ResidualAbsorptionResearchSpec",
    "StructuralDistanceFloorSpec",
]
