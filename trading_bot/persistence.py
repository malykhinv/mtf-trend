"""Persistence adapters for trading bot plans."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from trading_bot.datamodels.execution import ExecutionPlan


class AbstractPlanRepository(Protocol):
    """Repository interface for storing execution plans."""

    def save(self, plan: ExecutionPlan) -> None:
        """Persist ``plan`` to the underlying storage."""

    def load_latest(self) -> ExecutionPlan | None:
        """Return the most recently stored plan, if any."""


@dataclass(slots=True)
class FilePlanRepository:
    """Simple file-based repository implementation stub."""

    path: Path

    def save(self, plan: ExecutionPlan) -> None:  # pragma: no cover - stub
        """Serialize ``plan`` to :attr:`path`.

        The method intentionally raises :class:`NotImplementedError` until the
        serialization format is defined by subsequent commits.
        """

        raise NotImplementedError("FilePlanRepository.save is not implemented yet")

    def load_latest(self) -> ExecutionPlan | None:  # pragma: no cover - stub
        """Load the latest plan from :attr:`path` if available."""

        raise NotImplementedError("FilePlanRepository.load_latest is not implemented yet")
