"""Orchestrator ties together data, analysis and execution layers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from bot import config
from bot.data.accounting import BalanceProvider
from bot.data.diary import WorkbookDiary
from bot.data.loader import LiveBarEvent, LiveDataStream, MarketDataLoader
from bot.data.notifier import Notifier
from bot.utils.logging import get_logger
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
        self._latest_imbalance: dict[str, float] = {}
        self._symbols_above_threshold: set[str] = set()
        self._logger = get_logger(__name__)

    def start(self) -> None:
        self._deps.live_stream.subscribe(self._on_bar)

    def stop(self) -> None:
        self._deps.live_stream.close()

    def backfill(self, request) -> None:  # type: ignore[no-untyped-def]
        for bar in self._deps.market_loader.load(request):
            self._handle_bar(bar)

    def _on_bar(self, event: LiveBarEvent) -> None:
        bar = event.bar
        symbol_key = self._cooldown_key(bar)
        self._latest_imbalance[symbol_key] = event.imbalance
        if event.imbalance >= config.AGGR_IMBALANCE_THRESHOLD:
            self._symbols_above_threshold.add(symbol_key)
        else:
            self._symbols_above_threshold.discard(symbol_key)
        self._deps.execution.record_imbalance(symbol_key, event.imbalance)
        if self._is_on_cooldown(bar):
            return
        self._handle_bar(bar)

    def _handle_bar(self, bar: Bar) -> None:
        symbol_key = self._cooldown_key(bar)
        last_imbalance = self._latest_imbalance.get(symbol_key)
        if last_imbalance is None:
            last_imbalance = self._deps.execution.last_recorded_imbalance(symbol_key)

        if last_imbalance is None:
            self._symbols_above_threshold.discard(symbol_key)
        elif last_imbalance >= config.AGGR_IMBALANCE_THRESHOLD:
            self._symbols_above_threshold.add(symbol_key)
        else:
            self._symbols_above_threshold.discard(symbol_key)

        if (
            symbol_key not in self._symbols_above_threshold
            or last_imbalance is None
            or last_imbalance < config.AGGR_IMBALANCE_THRESHOLD
        ):
            return

        signal, anomaly = self._deps.analyzer.analyze_bar(bar)

        if anomaly is not None:
            self._deps.diary.append_anomalies([anomaly])

        if signal is None:
            return

        try:
            self._deps.notifier.send_signal(signal)
        except Exception:
            self._logger.exception("Ошибка отправки уведомления по сигналу %s", signal.signal_id)

        self._deps.diary.append_signals([signal])

        deposit = self._deps.balance_provider.current_deposit()
        order_size = self._deps.execution.calc_order_size_usdt(deposit_usdt=deposit)
        quantity = order_size / signal.levels.entry_price if signal.levels.entry_price else 0.0
        trade = self._deps.execution.open_trade(signal, quantity=quantity)
        self._deps.diary.append_trades([trade])

        try:
            self._deps.notifier.send_trade(trade)
        except Exception:
            self._logger.exception("Ошибка отправки уведомления по сделке %s", trade.trade_id)

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
