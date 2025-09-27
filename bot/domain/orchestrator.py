"""Orchestrator ties together data, analysis and execution layers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from bot import config
from bot.data.accounting import BalanceProvider
from bot.data.diary import WorkbookDiary
from bot.data.loader import LiveDataStream, MarketDataLoader
from bot.data.notifier import Notifier
from .analyzer import SignalAnalyzer
from .execution_service import ExecutionService
from .models.bar import Bar


@dataclass(slots=True)
class OrchestratorDependencies:
    market_loader: MarketDataLoader
    live_stream: LiveDataStream
    diary: WorkbookDiary
    notifier: Notifier
    analyzer: SignalAnalyzer
    execution: ExecutionService
    balance_provider: BalanceProvider


class Orchestrator:
    """Coordinates incoming data and delegates actions to other services."""

    def __init__(self, dependencies: OrchestratorDependencies) -> None:
        self._deps = dependencies
        self._last_processed: dict[str, datetime] = {}

    def start(self) -> None:
        self._deps.live_stream.subscribe(self._on_bar)

    def stop(self) -> None:
        self._deps.live_stream.close()

    def backfill(self, request) -> None:  # type: ignore[no-untyped-def]
        for bar in self._deps.market_loader.load(request):
            self._handle_bar(bar)

    def _on_bar(self, bar: Bar) -> None:
        if not self._should_process(bar):
            return
        self._handle_bar(bar)

    def _handle_bar(self, bar: Bar) -> None:
        signal, anomaly = self._deps.analyzer.analyze_bar(bar)

        if anomaly is not None:
            self._deps.diary.append_anomalies([anomaly])

        if signal is None:
            return

        self._deps.notifier.send_signal(signal)
        self._deps.diary.append_signals([signal])

        deposit = self._deps.balance_provider.current_deposit()
        order_size = self._deps.execution.calc_order_size_usdt(deposit_usdt=deposit)
        quantity = order_size / signal.levels.entry_price if signal.levels.entry_price else 0.0
        trade = self._deps.execution.open_trade(signal, quantity=quantity)
        self._deps.diary.append_trades([trade])
        self._register_processed(bar)

    def _should_process(self, bar: Bar) -> bool:
        key = self._bar_key(bar)
        last = self._last_processed.get(key)
        if last is None:
            return True
        return (datetime.now(tz=config.TIMEZONE) - last) >= timedelta(seconds=config.BAR_REPROCESS_THROTTLE_SEC)

    def _register_processed(self, bar: Bar) -> None:
        self._last_processed[self._bar_key(bar)] = datetime.now(tz=config.TIMEZONE)

    @staticmethod
    def _bar_key(bar: Bar) -> str:
        return f"{bar.exchange.value}:{bar.symbol}:{bar.timeframe.value}:{bar.bar_id}"
