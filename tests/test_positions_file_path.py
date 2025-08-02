import importlib
import json
import pathlib
import sys
import types
import asyncio

import pytest

# Ensure repository root on path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))


def load_strategy(monkeypatch):
    ai_stub = types.ModuleType("ai.parameter_optimizer")
    ai_stub.periodic_optimization = lambda *args, **kwargs: None
    ai_stub.load_thresholds = lambda *args, **kwargs: {}
    monkeypatch.setitem(sys.modules, "ai.parameter_optimizer", ai_stub)

    main_stub = types.ModuleType("main")
    main_stub.CONFIG = {}
    main_stub.WHITELISTS = {}
    monkeypatch.setitem(sys.modules, "main", main_stub)

    strategy = importlib.reload(importlib.import_module("strategies.funding_arbitrage"))
    return strategy, main_stub


def reset_state(strategy):
    strategy.positions.clear()
    from risk import risk_control
    risk_control._state = risk_control.RiskState()


def test_config_specifies_positions_file(tmp_path, monkeypatch):
    strategy, main = load_strategy(monkeypatch)
    reset_state(strategy)
    cfg_path = tmp_path / "config_positions.json"
    data = {"BTCUSDT": {"entry_timestamp": 1.0, "quantity": 1, "commissions": 0.0, "entry_futures_price": 100}}
    cfg_path.write_text(json.dumps(data))
    main.CONFIG.clear()
    main.CONFIG.update({"bot": {"positions_file": str(cfg_path)}})
    monkeypatch.delenv("POSITIONS_FILE_PATH", raising=False)

    asyncio.run(strategy.load_positions())
    assert "BTCUSDT" in strategy.positions

    asyncio.run(strategy.save_positions())
    saved = json.loads(cfg_path.read_text())
    assert "BTCUSDT" in saved


def test_env_var_overrides_config(tmp_path, monkeypatch):
    strategy, main = load_strategy(monkeypatch)
    reset_state(strategy)
    env_path = tmp_path / "env_positions.json"
    env_data = {"ETHUSDT": {"entry_timestamp": 1.0, "quantity": 2, "commissions": 0.0, "entry_futures_price": 200}}
    env_path.write_text(json.dumps(env_data))

    cfg_path = tmp_path / "other.json"
    cfg_path.write_text("{}")
    main.CONFIG.clear()
    main.CONFIG.update({"bot": {"positions_file": str(cfg_path)}})

    monkeypatch.setenv("POSITIONS_FILE_PATH", str(env_path))

    asyncio.run(strategy.load_positions())
    assert "ETHUSDT" in strategy.positions
    assert "BTCUSDT" not in strategy.positions

    asyncio.run(strategy.save_positions())
    saved = json.loads(env_path.read_text())
    assert "ETHUSDT" in saved
