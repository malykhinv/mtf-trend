"""Frozen Stage-0 protocol for blind session-anchored drawdown ladders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Literal

from anomaly_science.market_context.sessions import UTC_SESSION_CALENDAR_VERSION
from anomaly_science.validation.research_split import CalendarResearchSplit


DRAWDOWN_LADDER_RESEARCH_SPLIT = CalendarResearchSplit(
    split_version="drawdown_ladder_calendar_split_v1",
    is_start=date(2025, 6, 3),
    oos_start=date(2026, 1, 1),
    oos_end_exclusive=date(2026, 6, 18),
)


@dataclass(frozen=True, slots=True)
class DrawdownLadderStage0Spec:
    study_side: ClassVar[Literal["long", "short"]] = "long"
    event_family: ClassVar[str] = "session_drawdown"
    protocol_version: str = "drawdown_ladder_stage0_v1"
    protocol_freeze_id: str = "drawdown_ladder_stage0_20260802_v1"
    candidate_schema_version: str = "drawdown_ladder_candidate_v1"
    ladder_state_schema_version: str = "drawdown_ladder_state_candidate_v1"
    outcome_schema_version: str = "drawdown_ladder_recovery_outcome_v1"
    research_partition: str = "is"
    session_calendar_version: str = UTC_SESSION_CALENDAR_VERSION
    minimum_level_depth_pct: int = 3
    maximum_level_depth_pct: int = 90
    measurement_level_step_pct: int = 1
    registered_grid_steps_pct: tuple[int, ...] = (3, 5, 10)
    order_activation_delay_minutes: int = 1
    trade_through_bps: int = 5
    maximum_horizon_minutes: int = 48 * 60
    response_horizons_minutes: tuple[int, ...] = (5, 15, 60, 240, 720, 1440, 2880)
    round_trip_cost_bps: tuple[int, ...] = (10, 25, 50)
    equal_notional_ladder_states: bool = True

    def __post_init__(self) -> None:
        if self.research_partition != "is":
            raise ValueError("Stage 0 is physically locked to the IS partition")
        if self.session_calendar_version != UTC_SESSION_CALENDAR_VERSION:
            raise ValueError("unknown session calendar version")
        if not 0 < self.minimum_level_depth_pct < self.maximum_level_depth_pct < 100:
            raise ValueError("drawdown levels must satisfy 0 < minimum < maximum < 100")
        if self.measurement_level_step_pct <= 0:
            raise ValueError("measurement level step must be positive")
        if any(step <= 0 for step in self.registered_grid_steps_pct):
            raise ValueError("registered grid steps must be positive")
        if tuple(sorted(set(self.registered_grid_steps_pct))) != self.registered_grid_steps_pct:
            raise ValueError("registered grid steps must be sorted and unique")
        if self.order_activation_delay_minutes < 1:
            raise ValueError("orders require at least one full minute of activation delay")
        if self.trade_through_bps <= 0:
            raise ValueError("trade-through must be positive")
        if self.response_horizons_minutes[-1] != self.maximum_horizon_minutes:
            raise ValueError("the final response horizon must equal the maximum horizon")
        if tuple(sorted(set(self.response_horizons_minutes))) != self.response_horizons_minutes:
            raise ValueError("response horizons must be positive, sorted, and unique")
        if tuple(sorted(set(self.round_trip_cost_bps))) != self.round_trip_cost_bps:
            raise ValueError("cost buffers must be positive, sorted, and unique")
        if not self.equal_notional_ladder_states:
            raise ValueError("Stage-0 ladder states are frozen to equal-notional levels")

    @property
    def measurement_levels_pct(self) -> tuple[int, ...]:
        return tuple(
            range(
                self.minimum_level_depth_pct,
                self.maximum_level_depth_pct + 1,
                self.measurement_level_step_pct,
            )
        )

    @property
    def research_split(self) -> CalendarResearchSplit:
        return DRAWDOWN_LADDER_RESEARCH_SPLIT


@dataclass(frozen=True, slots=True)
class MirroredRallyStage0Spec(DrawdownLadderStage0Spec):
    """Mechanically mirrored short control; all non-directional rules are equal."""

    study_side: ClassVar[Literal["long", "short"]] = "short"
    event_family: ClassVar[str] = "session_rally_mirror"
    protocol_version: str = "mirrored_rally_stage0_v1"
    protocol_freeze_id: str = "mirrored_rally_stage0_20260802_v1"
    candidate_schema_version: str = "mirrored_rally_candidate_v1"
    ladder_state_schema_version: str = "mirrored_rally_state_candidate_v1"
    outcome_schema_version: str = "mirrored_short_recovery_outcome_v1"


__all__ = [
    "DRAWDOWN_LADDER_RESEARCH_SPLIT",
    "DrawdownLadderStage0Spec",
    "MirroredRallyStage0Spec",
]
