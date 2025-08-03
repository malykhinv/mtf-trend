import pathlib
import sys
import types
from dataclasses import dataclass, field
from decimal import Decimal
import importlib
import asyncio

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.mark.asyncio
async def test_monitor_position_final_message(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub heavy dependency sklearn
    linear_model_stub = types.SimpleNamespace(LinearRegression=object)
    sklearn_stub = types.SimpleNamespace(linear_model=linear_model_stub)
    monkeypatch.setitem(sys.modules, "sklearn", sklearn_stub)
    monkeypatch.setitem(sys.modules, "sklearn.linear_model", linear_model_stub)

    # Stub strategy module
    @dataclass
    class Position:
        entry_timestamp: float
        entry_futures_price: float
        entry_spot_price: float
        entry_basis: float
        entry_funding: float
        quantity: float
        initial_quantity: float
        pnl: Decimal = Decimal(0)
        exit_reasons: list[str] = field(default_factory=list)
        exit_timestamp: float = 0.0
        exit_funding: float | None = None
        exit_basis: float | None = None

    prepared = Position(
        entry_timestamp=0.0,
        entry_futures_price=100.0,
        entry_spot_price=100.0,
        entry_basis=0.0,
        entry_funding=0.001,
        quantity=1.0,
        initial_quantity=1.0,
        pnl=Decimal("2"),
        exit_reasons=["threshold"],
        exit_timestamp=10.0,
        exit_funding=0.0015,
        exit_basis=0.5,
    )

    async def monitor_neutral_position(*args, **kwargs):
        return prepared

    async def save_positions():
        return None

    strategy_stub = types.SimpleNamespace(
        monitor_neutral_position=monitor_neutral_position,
        positions={},
        save_positions=save_positions,
        Position=Position,
    )
    monkeypatch.setitem(sys.modules, "strategies.funding_arbitrage", strategy_stub)

    # Stub risk control
    async def _noop(*args, **kwargs):
        return None

    risk_control_stub = types.SimpleNamespace(
        update_position=_noop,
        mark_symbol_closed=_noop,
        is_paused=lambda: False,
        is_symbol_open=lambda symbol: False,
    )
    risk_pkg = types.SimpleNamespace(risk_control=risk_control_stub)
    monkeypatch.setitem(sys.modules, "risk", risk_pkg)
    monkeypatch.setitem(sys.modules, "risk.risk_control", risk_control_stub)

    # Stub parameter optimizer
    async def _noop_opt(*args, **kwargs):
        return None

    monkeypatch.setitem(
        sys.modules,
        "ai.parameter_optimizer",
        types.SimpleNamespace(periodic_optimization=_noop_opt),
    )

    main = importlib.reload(importlib.import_module("main"))

    main.CONFIG = {"thresholds": {}, "bot": {}}
    main.CLIENTS = {"stub": object()}

    messages: list[tuple[str, str]] = []

    async def _capture(position_id: str, text: str) -> None:
        messages.append((position_id, text))

    monkeypatch.setattr(main, "notify_close", _capture)

    await main.monitor_position("stub", "BTCUSDT", 1.0)
    await asyncio.sleep(0)  # allow notify_close task to run

    assert messages, "notify_close was not called"
    position_id, text = messages[0]
    assert position_id == "stub:BTCUSDT"
    assert "Закрыта BTCUSDT на stub" in text
    assert "Фандинг: 0.1500%" in text
    assert "Базис: 0.5000%" in text
    assert "Объём: $100.00" in text
    assert "Время в позиции: 10s" in text
    assert "PnL: 2.0000 (2.0000%) Причины: ['threshold']" in text
