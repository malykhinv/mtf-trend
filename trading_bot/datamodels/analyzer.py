"""Datamodels describing analyzer output rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from domain.models.enums import CandleInterval


@dataclass(frozen=True, slots=True)
class AnalyzerSnapshot:
    """Structured representation of a row returned by the pump analyzer.

    The analyzer that inspects multi-timeframe order flow and liquidation
    telemetry emits rich rows that historically were represented as loosely
    typed dictionaries.  The ``AnalyzerSnapshot`` dataclass captures the most
    relevant metrics in a stable schema so the trading bot orchestration layer
    can reason about the data without reaching into untyped payloads.
    """

    symbol: str
    interval: CandleInterval
    observed_at: datetime
    metrics: Mapping[str, float] = field(default_factory=dict)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def get_metric(self, name: str) -> float | None:
        """Return the value for ``name`` if present in :attr:`metrics`."""

        return self.metrics.get(name)
