"""Entry points for running the trading bot in different modes."""
from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import TYPE_CHECKING

from bot import config
from bot.config import BACKTEST_MIN_COVERAGE
from bot.data.accounting import CcxtBalanceProvider
from bot.data.diary import WorkbookDiary
from bot.data.exchange_utils import create_ccxt_client, fetch_linear_usdt_symbols
from bot.data.loader import CcxtMarketDataLoader, HistoricalRequest, WsLiveDataStream
from bot.data.notifier import Notifier, TelegramNotifier
from bot.domain.analyzer import SignalAnalyzer
from bot.domain.exchange_client import (
    BinanceExchangeClient,
    BybitExchangeClient,
    ExchangeClient,
    InMemoryExchangeClient,
)
from bot.domain.execution_service import ExecutionService
from bot.domain.models.exchange import Exchange
from bot.domain.models.runtime import BacktestSettings, RuntimeMode
from bot.domain.models.timeframe import Timeframe
from bot.domain.orchestrator import Orchestrator, OrchestratorDependencies
from bot.utils.logger import get_logger, setup_logging

if TYPE_CHECKING:
    from bot.domain.models.signal import Signal
    from bot.domain.models.trade import Trade


def run_live(dependencies: OrchestratorDependencies) -> Orchestrator:
    orchestrator = Orchestrator(dependencies)
    orchestrator.start()
    return orchestrator


def run_backtest(dependencies: OrchestratorDependencies, request: HistoricalRequest) -> None:
    orchestrator = Orchestrator(dependencies)
    orchestrator.backfill(request)


def create_exchange_client(exchange: Exchange) -> ExchangeClient:
    """Create an exchange client suitable for the selected exchange."""

    if exchange is Exchange.BINANCE and config.BINANCE_API_KEY and config.BINANCE_API_SECRET:
        return BinanceExchangeClient(
            api_key=config.BINANCE_API_KEY,
            api_secret=config.BINANCE_API_SECRET,
            base_url=config.BINANCE_API_URL,
            recv_window=config.BINANCE_RECV_WINDOW,
        )
    if exchange is Exchange.BYBIT and config.BYBIT_API_KEY and config.BYBIT_API_SECRET:
        return BybitExchangeClient(
            api_key=config.BYBIT_API_KEY,
            api_secret=config.BYBIT_API_SECRET,
            base_url=config.BYBIT_API_URL,
            recv_window=config.BYBIT_RECV_WINDOW,
            timeout=config.BYBIT_TIMEOUT,
        )
    return InMemoryExchangeClient()


def create_execution_service(exchange: Exchange) -> ExecutionService:
    """Factory that wires :class:`ExecutionService` for the given exchange."""

    exchange_client = create_exchange_client(exchange)
    return ExecutionService(exchange_client)


def create_market_data_loader() -> CcxtMarketDataLoader:
    return CcxtMarketDataLoader()


def create_live_stream(exchange: Exchange, timeframe: Timeframe) -> WsLiveDataStream:
    return WsLiveDataStream(exchange=exchange, timeframe=timeframe)


def create_diary(exchange: Exchange, base_path: Path | None = None) -> WorkbookDiary:
    base = Path(base_path) if base_path is not None else Path(
        os.getenv("BOT_DIARY_PATH", "var/diary")
    )
    path = base / exchange.value
    return WorkbookDiary(path=path)


class _NullNotifier(Notifier):
    """Fallback notifier that suppresses outbound notifications."""

    def send_signal(self, signal: Signal) -> None:  # pragma: no cover - simple no-op
        return None

    def send_trade(self, trade: Trade) -> None:  # pragma: no cover - simple no-op
        return None


def create_notifier(mode: RuntimeMode) -> Notifier:
    if mode is RuntimeMode.LIVE:
        try:
            return TelegramNotifier()
        except ValueError as exc:  # pragma: no cover - configuration-dependent
            get_logger(__name__).warning("Телеграм-уведомления отключены: %s", exc)
    return _NullNotifier()


def create_balance_provider(exchange: Exchange) -> CcxtBalanceProvider:
    client = create_ccxt_client(exchange)
    return CcxtBalanceProvider(client)


def create_orchestrator_dependencies(
    exchange: Exchange,
    mode: RuntimeMode,
    diary: WorkbookDiary | None = None,
) -> OrchestratorDependencies:
    primary_timeframe = config.DEFAULT_TIMEFRAMES[0]
    market_loader = create_market_data_loader()
    live_stream = create_live_stream(exchange, primary_timeframe)
    diary_instance = diary if diary is not None else create_diary(exchange)
    notifier = create_notifier(mode)
    analyzer = SignalAnalyzer()
    execution = create_execution_service(exchange)
    balance_provider = create_balance_provider(exchange)
    return OrchestratorDependencies(
        market_loader=market_loader,
        live_stream=live_stream,
        diary=diary_instance,
        notifier=notifier,
        analyzer=analyzer,
        execution=execution,
        balance_provider=balance_provider,
        exchange=exchange,
    )


def run() -> None:
    logger = setup_logging()
    mode = config.MODE
    exchange = config.EXCHANGE
    logger.info(
        "Инициализация режима %s для биржи %s", mode.value, exchange.value
    )
    if mode is RuntimeMode.LIVE:
        dependencies = create_orchestrator_dependencies(exchange, mode)
        try:
            run_live(dependencies)
        except KeyboardInterrupt:
            raise
        finally:
            dependencies.diary.close()
        return
    if mode is RuntimeMode.BACKTEST:
        end = datetime.datetime.now(tz=config.TIMEZONE)
        start = end - BACKTEST_MIN_COVERAGE
        settings = BacktestSettings(
            start=start,
            end=end,
            limit=None,
            timeframes=config.DEFAULT_TIMEFRAMES,
        )
        symbols = fetch_linear_usdt_symbols(exchange)
        if not symbols:
            logger.warning(
                "Не найдены линейные фьючерсы в USDT для биржи %s", exchange.value
            )
            return
        logger.info(
            "Запускаем бэктест для %s символов и %s таймфреймов",
            len(symbols),
            len(settings.timeframes),
        )
        diary = create_diary(exchange)
        try:
            for symbol in symbols:
                for timeframe in settings.timeframes:
                    dependencies = create_orchestrator_dependencies(
                        exchange, mode, diary=diary
                    )
                    request = HistoricalRequest(
                        exchange=exchange,
                        symbol=symbol,
                        timeframe=timeframe,
                        start=settings.start,
                        end=settings.end,
                        limit=settings.limit,
                        backtest=True,
                    )
                    run_backtest(dependencies, request)
        finally:
            diary.close()
        return


if __name__ == "__main__":
    run()
