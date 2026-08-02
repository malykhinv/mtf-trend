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
class MarketImpulseSpec:
    schema_version: str = "market_impulse_v1"
    primary_return_window_minutes: int = 15
    factor_return_windows_minutes: tuple[int, ...] = (5, 15, 30)
    same_type_baseline_sessions: int = 20
    minimum_baseline_snapshots: int = 100
    minimum_cross_section_symbols: int = 30
    primary_absolute_robust_z: float = 3.0
    sensitivity_absolute_robust_z: tuple[float, ...] = (2.5, 3.5)
    primary_directional_breadth: float = 0.60
    sensitivity_directional_breadth: tuple[float, ...] = (0.50, 0.70)
    event_cooldown_minutes: int = 60
    reference_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    response_horizons_minutes: tuple[int, ...] = (15, 30, 60, 120)

    def __post_init__(self) -> None:
        if self.factor_return_windows_minutes != (5, 15, 30):
            raise ValueError("market_impulse_v1 factor windows are fixed at 5/15/30 minutes")
        if self.primary_return_window_minutes not in self.factor_return_windows_minutes:
            raise ValueError("primary return window must be a registered factor window")
        if tuple(sorted(set(self.factor_return_windows_minutes))) != self.factor_return_windows_minutes:
            raise ValueError("factor return windows must be positive, sorted, and unique")
        if any(value <= 0 for value in self.factor_return_windows_minutes):
            raise ValueError("factor return windows must be positive")
        if self.same_type_baseline_sessions <= 0 or self.minimum_baseline_snapshots <= 0:
            raise ValueError("market impulse baseline requirements must be positive")
        if self.minimum_cross_section_symbols < 3:
            raise ValueError("market impulse cross-section requires at least three symbols")
        if self.primary_absolute_robust_z <= 0.0:
            raise ValueError("primary_absolute_robust_z must be positive")
        breadths = (self.primary_directional_breadth, *self.sensitivity_directional_breadth)
        if any(not 0.5 <= value <= 1.0 for value in breadths):
            raise ValueError("directional breadth thresholds must be in [0.5, 1]")
        if self.event_cooldown_minutes < self.primary_return_window_minutes:
            raise ValueError("event cooldown must cover the primary return window")
        if tuple(sorted(set(self.response_horizons_minutes))) != self.response_horizons_minutes:
            raise ValueError("response horizons must be positive, sorted, and unique")


@dataclass(frozen=True, slots=True)
class ResidualResponseSpec:
    schema_version: str = "residual_response_profile_v1"
    response_window_minutes: int = 15
    beta_history_same_type_sessions: int = 20
    beta_short_history_sessions: int = 5
    minimum_beta_observations: int = 100
    minimum_short_beta_observations: int = 25
    reference_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")

    def __post_init__(self) -> None:
        if self.response_window_minutes != 15:
            raise ValueError("residual_response_profile_v1 uses a fixed 15-minute response")
        if not 1 <= self.beta_short_history_sessions < self.beta_history_same_type_sessions:
            raise ValueError("short beta history must be smaller than full beta history")
        if self.minimum_beta_observations <= 2 or self.minimum_short_beta_observations <= 2:
            raise ValueError("beta estimates require at least three observations")
        if self.minimum_short_beta_observations >= self.minimum_beta_observations:
            raise ValueError("short beta minimum must be below full beta minimum")


@dataclass(frozen=True, slots=True)
class ResponseOutcomeSpec:
    schema_version: str = "residual_response_outcome_v1"
    path_interval_minutes: int = 5
    horizons_minutes: tuple[int, ...] = (15, 30, 60, 120)
    minimum_factor_symbols: int = 3
    reference_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")

    def __post_init__(self) -> None:
        if self.path_interval_minutes != 5:
            raise ValueError("residual_response_outcome_v1 uses five-minute paths")
        if self.horizons_minutes != (15, 30, 60, 120):
            raise ValueError("residual_response_outcome_v1 horizons are fixed")
        if self.minimum_factor_symbols < 2:
            raise ValueError("outcome factor requires at least two peer symbols")


@dataclass(frozen=True, slots=True)
class HighResolutionAggTradesSpec:
    """Locked raw-trade projection used only after coarse event selection."""

    schema_version: str = "residual_absorption_aggtrades_features_v1"
    source: str = "aggtrades"
    projection_schema_version: str = "binance_aggtrades_minute_v1"
    windows_minutes: tuple[int, ...] = (5, 15)
    selection_granularity_ms: int = 300_000
    enrichment_granularity_ms: int = 1
    primary_minimum_coverage: float = 1.0

    def __post_init__(self) -> None:
        if self.source != "aggtrades":
            raise ValueError("high-resolution v1 is fixed to aggtrades")
        if self.windows_minutes != (5, 15):
            raise ValueError("high-resolution v1 windows are fixed at 5/15 minutes")
        if self.selection_granularity_ms < 60_000:
            raise ValueError("high-resolution selection must remain coarse")
        if not 0.0 < self.enrichment_granularity_ms < self.selection_granularity_ms:
            raise ValueError("aggTrades granularity must be finer than selection")
        if self.primary_minimum_coverage != 1.0:
            raise ValueError("primary high-resolution arm requires complete minute coverage")


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
    market_impulse: MarketImpulseSpec = field(default_factory=MarketImpulseSpec)
    residual_response: ResidualResponseSpec = field(default_factory=ResidualResponseSpec)
    response_outcome: ResponseOutcomeSpec = field(default_factory=ResponseOutcomeSpec)
    high_resolution: HighResolutionAggTradesSpec = field(
        default_factory=HighResolutionAggTradesSpec
    )

    def __post_init__(self) -> None:
        if self.snapshot_interval_minutes != 5:
            raise ValueError("v1 snapshots are fixed at five-minute boundaries")
        if not self.allowed_high_resolution_sources:
            raise ValueError("at least one high-resolution source must be declared")


__all__ = [
    "RESIDUAL_ABSORPTION_RESEARCH_SPLIT",
    "MarketImpulseSpec",
    "ResidualResponseSpec",
    "ResponseOutcomeSpec",
    "HighResolutionAggTradesSpec",
    "ResidualAbsorptionResearchSpec",
    "StructuralDistanceFloorSpec",
]
