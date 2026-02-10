"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.models.reporting.quality_summary import QualitySummary
from domain.models.reporting.quality_symbol_stats import QualitySymbolStats


@dataclass(frozen=True, slots=True)
class QualityReport:
    summary: QualitySummary
    symbols: dict[str, QualitySymbolStats]
    recommendations: list[str]
