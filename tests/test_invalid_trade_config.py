import importlib
import logging
import pathlib
import sys
import types
import asyncio
from unittest.mock import AsyncMock

import pytest

# Ensure repository root on path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))


def test_no_trade_on_invalid_config(monkeypatch, caplog):
    # Stub heavy modules before importing main
    ai_stub = types.ModuleType("ai.parameter_optimizer")
    async def _noop(*args, **kwargs):
        return None
    ai_stub.periodic_optimization = _noop
    ai_stub.load_thresholds = lambda cfg: cfg
    monkeypatch.setitem(sys.modules, "ai.parameter_optimizer", ai_stub)

    strategy_stub = types.ModuleType("strategies.funding_arbitrage")
    strategy_stub.get_thresholds = lambda cfg: cfg
    strategy_stub.get_market_metrics = AsyncMock()
    strategy_stub.load_positions = _noop
    strategy_stub.positions = {}
    strategies_pkg = types.ModuleType("strategies")
    monkeypatch.setitem(sys.modules, "strategies", strategies_pkg)
    monkeypatch.setitem(
        sys.modules, "strategies.funding_arbitrage", strategy_stub
    )

    telegram_stub = types.ModuleType("utils.telegram")
    telegram_stub.format_duration = lambda x: ""
    telegram_stub.notify_close = _noop
    telegram_stub.notify_open = _noop
    monkeypatch.setitem(sys.modules, "utils.telegram", telegram_stub)

    main = importlib.import_module("main")

    # Prepare invalid configuration with infinite deposit
    main.CONFIG = {
        "bot": {"deposit_size": float("inf"), "poll_interval": 0},
        "thresholds": {"deposit_pct": 0.5, "min_trade_size": 10},
    }
    main.CLIENTS = {"x": object()}
    main.WHITELISTS = {"x": ["BTCUSDT"]}

    async def _false(*args, **kwargs):
        return False

    monkeypatch.setattr(main.risk_control, "is_paused", _false)
    monkeypatch.setattr(main.risk_control, "is_symbol_open", _false)

    captured = {}

    def fake_create_task(coro, *args, **kwargs):
        if "scan_loop" in getattr(coro, "__qualname__", ""):
            captured["scan_loop"] = coro
        else:
            coro.close()
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        return fut

    async def fake_gather(*aws, **kwargs):
        return None

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(asyncio, "gather", fake_gather)

    # This call will capture scan_loop but not execute it
    main.start_processing_loops()
    scan_coro = captured["scan_loop"]

    async def fake_sleep(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    caplog.set_level(logging.ERROR)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scan_coro)

    assert "Некорректное значение trade_value" in caplog.text
    strategy_stub.get_market_metrics.assert_not_called()
