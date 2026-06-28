from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class StructuralAnchor(str, Enum):
    """Point-in-time price structures that may anchor execution rules."""

    RUNNING_HIGH = "running_high_asof_t"
    RUNNING_LOW = "running_low_asof_t"
    CONFIRMED_SWING_HIGH = "confirmed_swing_high_asof_t"
    CONFIRMED_SWING_LOW = "confirmed_swing_low_asof_t"
    EVENT_MAIN_HIGH = "event_main_high_asof_t"
    EVENT_BASE = "event_base_asof_t"


class BarrierTrigger(str, Enum):
    TOUCH = "touch"
    CLOSE_BEYOND = "close_beyond"


@dataclass(frozen=True, slots=True)
class StructuralStopPolicy:
    policy_id: str
    side: PositionSide
    initial_anchor: StructuralAnchor
    trigger: BarrierTrigger
    trailing_anchor: StructuralAnchor | None = None
    swing_confirmation_bars: int = 2

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("stop policy_id is required")
        if self.swing_confirmation_bars < 1:
            raise ValueError("swing_confirmation_bars must be positive")
        if self.trailing_anchor is not None:
            expected = (
                StructuralAnchor.CONFIRMED_SWING_LOW
                if self.side is PositionSide.LONG
                else StructuralAnchor.CONFIRMED_SWING_HIGH
            )
            if self.trailing_anchor is not expected:
                raise ValueError(f"{self.side.value} trailing stop must use {expected.value}")


@dataclass(frozen=True, slots=True)
class StructuralTakeProfitPolicy:
    policy_id: str
    side: PositionSide
    anchor: StructuralAnchor
    trigger: BarrierTrigger = BarrierTrigger.TOUCH
    close_fraction_grid: tuple[float, ...] = (1.0,)

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("take-profit policy_id is required")
        if not self.close_fraction_grid:
            raise ValueError("close_fraction_grid must not be empty")
        if tuple(sorted(set(self.close_fraction_grid))) != self.close_fraction_grid:
            raise ValueError("close_fraction_grid must be sorted and unique")
        if any(value <= 0.0 or value > 1.0 for value in self.close_fraction_grid):
            raise ValueError("close fractions must be inside (0, 1]")


@dataclass(frozen=True, slots=True)
class StrategyExecutionPolicies:
    policy_version: str
    stop_policies: tuple[StructuralStopPolicy, ...]
    take_profit_policies: tuple[StructuralTakeProfitPolicy, ...] = ()

    def __post_init__(self) -> None:
        if not self.policy_version:
            raise ValueError("execution policy_version is required")
        if not self.stop_policies:
            raise ValueError("at least one structural stop policy is required")
        identifiers = [policy.policy_id for policy in (*self.stop_policies, *self.take_profit_policies)]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("execution policy ids must be unique")

    def stop_for_side(self, side: PositionSide) -> StructuralStopPolicy:
        matches = tuple(policy for policy in self.stop_policies if policy.side is side)
        if len(matches) != 1:
            raise ValueError(f"exactly one stop policy is required for {side.value}; got {len(matches)}")
        return matches[0]

    def take_profits_for_side(self, side: PositionSide) -> tuple[StructuralTakeProfitPolicy, ...]:
        return tuple(policy for policy in self.take_profit_policies if policy.side is side)


PUMP_FADE_EXECUTION_POLICIES = StrategyExecutionPolicies(
    policy_version="pump_fade_structural_execution_v1",
    stop_policies=(
        StructuralStopPolicy(
            policy_id="short_main_high_close_then_swing_high_trail",
            side=PositionSide.SHORT,
            initial_anchor=StructuralAnchor.EVENT_MAIN_HIGH,
            trigger=BarrierTrigger.CLOSE_BEYOND,
            trailing_anchor=StructuralAnchor.CONFIRMED_SWING_HIGH,
            swing_confirmation_bars=2,
        ),
    ),
    take_profit_policies=(
        StructuralTakeProfitPolicy(
            policy_id="short_partial_at_event_base",
            side=PositionSide.SHORT,
            anchor=StructuralAnchor.EVENT_BASE,
            trigger=BarrierTrigger.TOUCH,
            close_fraction_grid=(0.25, 0.5, 0.75, 1.0),
        ),
    ),
)


GENERIC_ANOMALY_EXECUTION_POLICIES = StrategyExecutionPolicies(
    policy_version="generic_anomaly_structural_execution_v1",
    stop_policies=(
        StructuralStopPolicy(
            policy_id="long_running_low_close_then_swing_low_trail",
            side=PositionSide.LONG,
            initial_anchor=StructuralAnchor.RUNNING_LOW,
            trigger=BarrierTrigger.CLOSE_BEYOND,
            trailing_anchor=StructuralAnchor.CONFIRMED_SWING_LOW,
        ),
        StructuralStopPolicy(
            policy_id="short_running_high_close_then_swing_high_trail",
            side=PositionSide.SHORT,
            initial_anchor=StructuralAnchor.RUNNING_HIGH,
            trigger=BarrierTrigger.CLOSE_BEYOND,
            trailing_anchor=StructuralAnchor.CONFIRMED_SWING_HIGH,
        ),
    ),
    take_profit_policies=(
        StructuralTakeProfitPolicy(
            policy_id="long_partial_at_running_high",
            side=PositionSide.LONG,
            anchor=StructuralAnchor.RUNNING_HIGH,
            close_fraction_grid=(0.25, 0.5, 0.75, 1.0),
        ),
        StructuralTakeProfitPolicy(
            policy_id="short_partial_at_running_low",
            side=PositionSide.SHORT,
            anchor=StructuralAnchor.RUNNING_LOW,
            close_fraction_grid=(0.25, 0.5, 0.75, 1.0),
        ),
    ),
)
