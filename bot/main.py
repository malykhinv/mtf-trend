"""Entry points for running the trading bot in different modes."""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

import ccxt

from bot import config
from bot.data.accounting import CcxtBalanceProvider
from bot.data.diary import WorkbookDiary
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
from bot.utils.datetime import parse_iso_datetime
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


def create_live_stream(timeframe: Timeframe) -> WsLiveDataStream:
    return WsLiveDataStream(timeframe=timeframe)


def create_diary(base_path: Path | None = None) -> WorkbookDiary:
    path = base_path or Path(os.getenv("BOT_DIARY_PATH", "var/diary"))
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


def _create_ccxt_client(exchange: Exchange) -> object:
    if exchange is Exchange.BINANCE:
        params: dict[str, object] = {"enableRateLimit": True}
        if config.BINANCE_API_KEY and config.BINANCE_API_SECRET:
            params.update({"apiKey": config.BINANCE_API_KEY, "secret": config.BINANCE_API_SECRET})
        return ccxt.binanceusdm(params)
    if exchange is Exchange.BYBIT:
        params: dict[str, object] = {
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "defaultSubType": "linear",
                "defaultSettle": "USDT",
            },
        }
        if config.BYBIT_API_KEY and config.BYBIT_API_SECRET:
            params.update({"apiKey": config.BYBIT_API_KEY, "secret": config.BYBIT_API_SECRET})
        return ccxt.bybit(params)
    raise ValueError(f"Unsupported exchange: {exchange}")


def create_balance_provider(exchange: Exchange) -> CcxtBalanceProvider:
    client = _create_ccxt_client(exchange)
    return CcxtBalanceProvider(client)


def create_orchestrator_dependencies(
    exchange: Exchange, mode: RuntimeMode
) -> OrchestratorDependencies:
    primary_timeframe = config.DEFAULT_TIMEFRAMES[0]
    market_loader = create_market_data_loader()
    live_stream = create_live_stream(primary_timeframe)
    diary = create_diary()
    notifier = create_notifier(mode)
    analyzer = SignalAnalyzer()
    execution = create_execution_service(exchange)
    balance_provider = create_balance_provider(exchange)
    return OrchestratorDependencies(
        market_loader=market_loader,
        live_stream=live_stream,
        diary=diary,
        notifier=notifier,
        analyzer=analyzer,
        execution=execution,
        balance_provider=balance_provider,
    )


def create_backtest_request(exchange: Exchange, settings: BacktestSettings) -> HistoricalRequest:
    return HistoricalRequest(
        exchange=exchange,
        symbol=settings.symbol,
        timeframe=settings.timeframe,
        start=settings.start,
        end=settings.end,
        limit=settings.limit,
        backtest=True,
    )


def read_runtime_mode(env: Mapping[str, str]) -> RuntimeMode:
    raw = env.get("BOT_MODE")
    if raw is None:
        return RuntimeMode.LIVE
    try:
        return RuntimeMode(raw.lower())
    except ValueError as exc:
        raise ValueError(f"Unsupported runtime mode: {raw}") from exc


def read_exchange(env: Mapping[str, str]) -> Exchange:
    raw = env.get("BOT_EXCHANGE")
    if raw is None:
        return Exchange.BINANCE
    try:
        return Exchange(raw.lower())
    except ValueError as exc:
        raise ValueError(f"Unsupported exchange: {raw}") from exc


def read_backtest_settings(env: Mapping[str, str]) -> BacktestSettings:
    symbol = env.get("BOT_BACKTEST_SYMBOL")
    timeframe_raw = env.get("BOT_BACKTEST_TIMEFRAME")
    start_raw = env.get("BOT_BACKTEST_START")
    end_raw = env.get("BOT_BACKTEST_END")

    missing = [
        name
        for name, value in {
            "BOT_BACKTEST_SYMBOL": symbol,
            "BOT_BACKTEST_TIMEFRAME": timeframe_raw,
            "BOT_BACKTEST_START": start_raw,
            "BOT_BACKTEST_END": end_raw,
        }.items()
        if not value
    ]
    if missing:
        missing_vars = ", ".join(missing)
        raise ValueError(f"Missing backtest configuration: {missing_vars}")

    limit_raw = env.get("BOT_BACKTEST_LIMIT")
    limit = int(limit_raw) if limit_raw else None
    try:
        timeframe = Timeframe((timeframe_raw or "").lower())
    except ValueError as exc:
        raise ValueError(f"Unsupported backtest timeframe: {timeframe_raw}") from exc

    start = parse_iso_datetime(start_raw or "", timezone=config.TIMEZONE)
    end = parse_iso_datetime(end_raw or "", timezone=config.TIMEZONE)

    return BacktestSettings(
        symbol=symbol or "",
        timeframe=timeframe,
        start=start,
        end=end,
        limit=limit,
    )


def run() -> None:
    logger = setup_logging()
    env = os.environ
    mode = read_runtime_mode(env)
    exchange = read_exchange(env)
    logger.info(
        "Инициализация режима %s для биржи %s", mode.value, exchange.value
    )
    dependencies = create_orchestrator_dependencies(exchange, mode)
    if mode is RuntimeMode.LIVE:
        run_live(dependencies)
        return
    if mode is RuntimeMode.BACKTEST:
        settings = read_backtest_settings(env)
        request = create_backtest_request(exchange, settings)
        run_backtest(dependencies, request)
        return
    raise ValueError(f"Unsupported runtime mode: {mode}")


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    run()
