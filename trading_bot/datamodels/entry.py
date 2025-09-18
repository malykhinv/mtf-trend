"""Entry evaluation datamodels."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EntryThresholds:
    """Quantitative thresholds required for trade entry consideration."""

    min_trend_score: float
    max_open_interest_delta_pct: float
    max_taker_ratio: float
    max_premium_pct: float
    latency_sec: float


@dataclass(frozen=True, slots=True)
class EntryTrigger:
    """Outcome of evaluating a snapshot against :class:`EntryThresholds`."""

    meets_trend: bool
    meets_confirmations: bool
    rationale: tuple[str, ...]

    @property
    def is_eligible(self) -> bool:
        """Whether both trend and confirmation gates were satisfied."""

        return self.meets_trend and self.meets_confirmations
