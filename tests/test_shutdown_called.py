import importlib
import sys
import types
import asyncio
from unittest.mock import AsyncMock

import pathlib

# Ensure repository root on path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))


def test_shutdown_called(monkeypatch):
    # Stub heavy modules before importing main
    ai_stub = types.ModuleType("ai.parameter_optimizer")
    async def _noop(*args, **kwargs):
        return None
    ai_stub.periodic_optimization = _noop
    ai_stub.load_thresholds = lambda cfg: cfg
    monkeypatch.setitem(sys.modules, "ai.parameter_optimizer", ai_stub)

    strategy_stub = types.ModuleType("strategies.funding_arbitrage")
    strategy_stub.get_thresholds = lambda cfg: cfg
    strategy_stub.load_positions = _noop
    strategy_stub.positions = {}
    strategies_pkg = types.ModuleType("strategies")
    monkeypatch.setitem(sys.modules, "strategies", strategies_pkg)
    monkeypatch.setitem(sys.modules, "strategies.funding_arbitrage", strategy_stub)

    telegram_stub = types.ModuleType("utils.telegram")
    telegram_stub.format_duration = lambda x: ""
    telegram_stub.notify_close = _noop
    telegram_stub.notify_open = _noop
    telegram_stub.shutdown = _noop
    monkeypatch.setitem(sys.modules, "utils.telegram", telegram_stub)

    main = importlib.import_module("main")

    shutdown_mock = AsyncMock()
    monkeypatch.setattr(main, "shutdown", shutdown_mock)

    async def fake_gather(*aws, **kwargs):
        return None

    def fake_create_task(coro, *args, **kwargs):
        coro.close()
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        fut.set_result(None)
        return fut

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(asyncio, "gather", fake_gather)

    main.CONFIG = {"bot": {}, "thresholds": {}, "risk": {}}
    main.CLIENTS = {}
    main.WHITELISTS = {}
    main.POSITION_TASKS = {}

    main.start_processing_loops()

    assert shutdown_mock.await_count == 1

    sys.modules.pop("main", None)
