"""Tests for orchestrator handling based on trade print imbalance."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
import types

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

openpyxl_stub = types.ModuleType("openpyxl")
openpyxl_stub.Workbook = lambda: None
openpyxl_stub.load_workbook = lambda *args, **kwargs: None
sys.modules.setdefault("openpyxl", openpyxl_stub)

ccxt_stub = types.ModuleType("ccxt")
ccxt_stub.binanceusdm = lambda *args, **kwargs: object()
ccxt_stub.bybit = lambda *args, **kwargs: object()
sys.modules.setdefault("ccxt", ccxt_stub)

websocket_stub = types.ModuleType("websocket")


class _StubWebSocketApp:  # pragma: no cover - helper for dependency stubbing
    def __init__(self, *args, **kwargs) -> None:
        pass

    def run_forever(self, *args, **kwargs) -> None:
        pass

    def send(self, *args, **kwargs) -> None:
        pass


websocket_stub.WebSocketApp = _StubWebSocketApp
sys.modules.setdefault("websocket", websocket_stub)

requests_stub = types.ModuleType("requests")


class _StubResponse:  # pragma: no cover - helper for dependency stubbing
    status_code = 200

    def raise_for_status(self) -> None:
        pass


requests_stub.Response = _StubResponse
requests_stub.post = lambda *args, **kwargs: _StubResponse()
sys.modules.setdefault("requests", requests_stub)

from bot import config
from bot.domain.models.bar import Bar, BarMetrics, BreakDirection
from bot.domain.models.exchange import Exchange
from bot.domain.models.signal import Signal, SignalLevels, ThresholdSnapshot
from bot.domain.models.signal_direction import SignalDirection
from bot.domain.models.timeframe import Timeframe
from bot.domain.orchestrator import Orchestrator, OrchestratorDependencies
from bot.domain.execution_service import ExecutionService


class _DummyMarketLoader:
    def load(self, request):  # pragma: no cover - not used in tests
        return []


class _DummyLiveStream:
    def subscribe(self, listener):  # pragma: no cover - not used in tests
        self._listener = listener

    def close(self):  # pragma: no cover - not used in tests
        pass


class _RecordingDiary:
    def __init__(self) -> None:
        self.anomalies = []
        self.signals = []
        self.trades = []

    def append_anomalies(self, anomalies):
        self.anomalies.extend(anomalies)

    def append_signals(self, signals):
        self.signals.extend(signals)

    def append_trades(self, trades):
        self.trades.extend(trades)


class _SilentNotifier:
    def __init__(self) -> None:
        self.signals = []
        self.trades = []

    def send_signal(self, signal):
        self.signals.append(signal)

    def send_trade(self, trade):
        self.trades.append(trade)


class _StaticBalanceProvider:
    def __init__(self, deposit: float) -> None:
        self._deposit = deposit

    def current_deposit(self) -> float:
        return self._deposit


class _StubAnalyzer:
    def __init__(self, signal: Signal) -> None:
        self._signal = signal

    def analyze_bar(self, bar):
        return self._signal, None


class _RecordingExecutionService(ExecutionService):
    def __init__(self) -> None:
        super().__init__()
        self.open_calls: list[tuple[Signal, float]] = []

    def open_trade(self, signal, quantity, timestamp=None):  # type: ignore[override]
        self.open_calls.append((signal, quantity))
        return super().open_trade(signal, quantity, timestamp)


def _build_bar() -> Bar:
    now = datetime.now(tz=config.TIMEZONE)
    metrics = BarMetrics(
        pct_move=5.0,
        relative_volume=50.0,
        atr_mult=6.0,
        upper_wick_pct=10.0,
        body_pct=50.0,
        lower_wick_pct=10.0,
        pct_to_low_break=0.0,
        pct_to_high_break=0.0,
        break_direction=BreakDirection.NONE,
    )
    return Bar(
        bar_id="bar-1",
        exchange=Exchange.BINANCE,
        symbol="BTC/USDT",
        timeframe=Timeframe.M1,
        open_time=now - timedelta(minutes=1),
        close_time=now,
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=1000.0,
        metrics=metrics,
    )


def _build_signal(bar: Bar) -> Signal:
    thresholds = ThresholdSnapshot(
        min_green_move_pct=0.0,
        min_volume_spike=0.0,
        min_relative_volume=0.0,
        max_relative_volume=200.0,
        min_atr_mult=0.0,
        min_pct_move=0.0,
        max_pct_move=100.0,
        max_upper_wick_pct=100.0,
        max_lower_wick_pct=100.0,
    )
    levels = SignalLevels(entry_price=105.0, take_profit_price=110.0, stop_loss_price=95.0)
    return Signal(
        signal_id="signal-1",
        bar=bar,
        timestamp=bar.close_time,
        exchange=bar.exchange,
        symbol=bar.symbol,
        timeframe=bar.timeframe,
        thresholds=thresholds,
        direction=SignalDirection.LONG,
        levels=levels,
    )


@pytest.fixture()
def orchestrator_setup():
    bar = _build_bar()
    signal = _build_signal(bar)
    execution = _RecordingExecutionService()
    dependencies = OrchestratorDependencies(
        market_loader=_DummyMarketLoader(),
        live_stream=_DummyLiveStream(),
        diary=_RecordingDiary(),
        notifier=_SilentNotifier(),
        analyzer=_StubAnalyzer(signal),
        execution=execution,
        balance_provider=_StaticBalanceProvider(10_000.0),
    )
    orchestrator = Orchestrator(dependencies)
    return orchestrator, execution, bar


def test_no_trade_when_imbalance_below_threshold(orchestrator_setup):
    orchestrator, execution, bar = orchestrator_setup
    symbol_key = orchestrator._cooldown_key(bar)
    execution.record_imbalance(symbol_key, config.AGGR_IMBALANCE_THRESHOLD - 0.1)

    orchestrator._handle_bar(bar)

    assert execution.open_calls == []


def test_trade_attempt_when_imbalance_above_threshold(orchestrator_setup):
    orchestrator, execution, bar = orchestrator_setup
    symbol_key = orchestrator._cooldown_key(bar)
    execution.record_imbalance(symbol_key, config.AGGR_IMBALANCE_THRESHOLD + 0.1)

    orchestrator._handle_bar(bar)

    assert len(execution.open_calls) == 1
