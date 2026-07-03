"""Stable Core contract for strategy-declared structural execution policies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class StructuralAnchor(str, Enum):
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
