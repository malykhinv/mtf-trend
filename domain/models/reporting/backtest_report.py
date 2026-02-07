"""DTO for final backtest report output."""

from __future__ import annotations

from dataclasses import dataclass

from domain.models.reporting.backtest_summary import BacktestSummary
from domain.models.reporting.optimal_parameter_ranges import OptimalParameterRanges
from domain.models.reporting.trade_results_distribution import TradeResultsDistribution


@dataclass(frozen=True, slots=True)
class BacktestReport:
    summary: BacktestSummary
    optimal_ranges: OptimalParameterRanges
    trade_results_distribution: TradeResultsDistribution
