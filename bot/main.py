"""Entry points for running the trading bot in different modes."""
from __future__ import annotations

from bot import config
from bot.data.loader import HistoricalRequest
from bot.domain.exchange_client import BinanceExchangeClient, ExchangeClient, InMemoryExchangeClient
from bot.domain.execution_service import ExecutionService
from bot.domain.orchestrator import Orchestrator, OrchestratorDependencies


def run_live(dependencies: OrchestratorDependencies) -> Orchestrator:
    orchestrator = Orchestrator(dependencies)
    orchestrator.start()
    return orchestrator


def run_backtest(dependencies: OrchestratorDependencies, request: HistoricalRequest) -> None:
    orchestrator = Orchestrator(dependencies)
    orchestrator.backfill(request)


def create_exchange_client() -> ExchangeClient:
    """Create an exchange client suitable for the current runtime."""

    if config.BINANCE_API_KEY and config.BINANCE_API_SECRET:
        return BinanceExchangeClient(
            api_key=config.BINANCE_API_KEY,
            api_secret=config.BINANCE_API_SECRET,
            base_url=config.BINANCE_API_URL,
            recv_window=config.BINANCE_RECV_WINDOW,
        )
    return InMemoryExchangeClient()


def create_execution_service() -> ExecutionService:
    """Factory that wires :class:`ExecutionService` for live trading."""

    exchange_client = create_exchange_client()
    return ExecutionService(exchange_client)
