"""Trade plan assembly scaffolding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading_bot.datamodels.analyzer import AnalyzerSnapshot
from trading_bot.datamodels.execution import ExecutionPlan
from trading_bot.datamodels.entry import EntryTrigger


class PlanBuilder(Protocol):
    """Protocol describing a component that can build execution plans."""

    def build(self, snapshot: AnalyzerSnapshot, trigger: EntryTrigger) -> ExecutionPlan:
        """Produce an execution plan for the given inputs."""


@dataclass(slots=True)
class TradePlanAssembler:
    """Coordinate entry evaluation results with execution plan generation."""

    builder: PlanBuilder

    def assemble(self, snapshot: AnalyzerSnapshot, trigger: EntryTrigger) -> ExecutionPlan:
        """Return an execution plan, delegating to the configured ``builder``."""

        return self.builder.build(snapshot, trigger)
