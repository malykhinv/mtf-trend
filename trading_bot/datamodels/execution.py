"""Execution plan datamodels."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Iterable

from domain.models.enums import Side


class ExecutionPlanStatus(Enum):
    """Lifecycle states for an :class:`ExecutionPlan`."""

    PENDING = auto()
    SUBMITTED = auto()
    FILLED = auto()
    CANCELLED = auto()


@dataclass(frozen=True, slots=True)
class ExecutionLeg:
    """Single order leg that composes an execution plan."""

    side: Side
    size: float
    price: float | None
    order_type: str


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Collection of :class:`ExecutionLeg` describing the target position."""

    legs: tuple[ExecutionLeg, ...]
    status: ExecutionPlanStatus = ExecutionPlanStatus.PENDING

    def __post_init__(self) -> None:
        if not self.legs:
            msg = "ExecutionPlan requires at least one leg"
            raise ValueError(msg)

    def total_size(self) -> float:
        """Compute the aggregate order size of the plan."""

        return sum(leg.size for leg in self.legs)

    def iter_legs(self) -> Iterable[ExecutionLeg]:
        """Yield the plan legs in execution order."""

        return iter(self.legs)
