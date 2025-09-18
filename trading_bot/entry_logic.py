"""Entry evaluation scaffolding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading_bot.datamodels.analyzer import AnalyzerSnapshot
from trading_bot.datamodels.entry import EntryThresholds, EntryTrigger


class ThresholdProvider(Protocol):
    """Interface describing configuration sources for entry thresholds."""

    def get_thresholds(self, symbol: str) -> EntryThresholds:
        """Return thresholds that should be applied for ``symbol``."""


@dataclass(slots=True)
class EntryEvaluator:
    """Evaluate analyzer snapshots against configured entry guardrails.

    Concrete logic will be implemented in subsequent iterations.  The current
    stub documents the orchestration-facing interface so the wider codebase can
    reference it without immediately pulling in complex trading logic.
    """

    threshold_provider: ThresholdProvider

    def evaluate(self, snapshot: AnalyzerSnapshot) -> EntryTrigger:
        """Evaluate ``snapshot`` and return an :class:`EntryTrigger` result."""

        thresholds = self.threshold_provider.get_thresholds(snapshot.symbol)
        rationale: tuple[str, ...] = (
            "entry logic not implemented",
            f"thresholds: {thresholds}",
        )
        return EntryTrigger(
            meets_trend=False,
            meets_confirmations=False,
            rationale=rationale,
        )
