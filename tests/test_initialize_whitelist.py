import importlib
import logging
import pathlib
import sys
import types

import pytest

# Ensure repository root on path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))


def test_initialize_filters_whitelist(monkeypatch, caplog):
    # Provide lightweight stand-ins for heavy modules
    ai_stub = types.ModuleType("ai.parameter_optimizer")
    ai_stub.periodic_optimization = lambda *args, **kwargs: None
    ai_stub.load_thresholds = lambda *args, **kwargs: {}
    monkeypatch.setitem(sys.modules, "ai.parameter_optimizer", ai_stub)

    strategy_stub = types.ModuleType("strategies.funding_arbitrage")
    strategy_stub.get_thresholds = lambda cfg: cfg

    async def _noop(*args, **kwargs):
        return None

    strategy_stub.load_positions = _noop
    strategy_stub.positions = {}
    strategies_pkg = types.ModuleType("strategies")
    monkeypatch.setitem(sys.modules, "strategies", strategies_pkg)
    monkeypatch.setitem(sys.modules, "strategies.funding_arbitrage", strategy_stub)

    # Import modules after stubbing
    main = importlib.import_module("main")
    BinanceExchange = importlib.import_module("exchanges.binance").BinanceExchange
    BybitExchange = importlib.import_module("exchanges.bybit").BybitExchange

    # Prepare dummy config
    main.CONFIG = {
        "api_keys": {"binance": "k", "secret": "s", "bybit": "k2", "bybit_secret": "s2"},
        "bot": {"whitelist": ["BTCUSDT", "ETHUSDT", "FOOUSD"], "deposit_size": 100},
        "risk": {},
    }
    main.CLIENTS.clear()
    main.WHITELISTS.clear()

    monkeypatch.setattr(main.risk_control, "configure", lambda *args, **kwargs: None)

    async def futures_ok(self):
        return ["BTCUSDT", "ETHUSDT"]

    async def spot_ok(self):
        return ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

    async def futures_bybit(self):
        return ["BTCUSDT"]

    async def spot_bybit(self):
        return ["BTCUSDT"]

    monkeypatch.setattr(BinanceExchange, "get_futures_symbols", futures_ok)
    monkeypatch.setattr(BinanceExchange, "get_spot_symbols", spot_ok)
    monkeypatch.setattr(BybitExchange, "get_futures_symbols", futures_bybit)
    monkeypatch.setattr(BybitExchange, "get_spot_symbols", spot_bybit)

    caplog.set_level(logging.WARNING)
    main.initialize_bot()

    assert main.WHITELISTS["binance"] == ["BTCUSDT", "ETHUSDT"]
    assert main.WHITELISTS["bybit"] == ["BTCUSDT"]

    warnings = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING]
    joined = "\n".join(warnings)
    assert "FOOUSD" in joined and "ETHUSDT" in joined
