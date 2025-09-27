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
        self._symbol_cooldown: dict[str, datetime] = {}

    def start(self) -> None:
        self._deps.live_stream.subscribe(self._on_bar)

    def stop(self) -> None:
        self._deps.live_stream.close()

    def backfill(self, request) -> None:  # type: ignore[no-untyped-def]
        for bar in self._deps.market_loader.load(request):
            self._handle_bar(bar)

    def _on_bar(self, bar: Bar) -> None:
        if self._is_on_cooldown(bar):
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

        if anomaly is not None:
            self._activate_cooldown(bar)

    def _is_on_cooldown(self, bar: Bar) -> bool:
        key = self._cooldown_key(bar)
        until = self._symbol_cooldown.get(key)
        if until is None:
            return False
        now = datetime.now(tz=config.TIMEZONE)
        if now >= until:
            self._symbol_cooldown.pop(key, None)
            return False
        return True

    def _activate_cooldown(self, bar: Bar) -> None:
        self._symbol_cooldown[self._cooldown_key(bar)] = datetime.now(tz=config.TIMEZONE) + timedelta(
            seconds=config.SYMBOL_COOLDOWN_SEC
        )

    @staticmethod
    def _cooldown_key(bar: Bar) -> str:
        return f"{bar.exchange.value}:{bar.symbol}"
