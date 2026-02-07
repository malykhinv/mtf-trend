"""DTO for final backtest report output."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BacktestReport:
    summary: dict[str, int | float]
    optimal_ranges: dict[str, list[int | float]]
    trade_results_distribution: dict[str, int]
