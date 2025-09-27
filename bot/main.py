"""Entry points for running the trading bot in different modes."""
from __future__ import annotations

from bot.data.loader import HistoricalRequest
from bot.domain.orchestrator import Orchestrator, OrchestratorDependencies


def run_live(dependencies: OrchestratorDependencies) -> Orchestrator:
    orchestrator = Orchestrator(dependencies)
    orchestrator.start()
    return orchestrator


def run_backtest(dependencies: OrchestratorDependencies, request: HistoricalRequest) -> None:
    orchestrator = Orchestrator(dependencies)
    orchestrator.backfill(request)
