"""Aggregate quality summary DTO."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QualitySummary:
    symbols_checked: int
    issues_total: int
    gaps_total: int
    by_issue_type: dict[str, int]
    by_severity: dict[str, int]
