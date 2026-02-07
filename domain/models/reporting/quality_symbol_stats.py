"""Per-symbol quality stats DTO."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QualitySymbolStats:
    issues: int
    gaps: int
    by_issue_type: dict[str, int]
    by_severity: dict[str, int]
