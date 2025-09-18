import asyncio

import pytest

from bot.app import main_async, resolve_mode
from bot.data.io.config_loader import AppConfig


class _DummyProvider:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:  # pragma: no cover - trivial
        self.closed = True


def test_main_async_runs_backtest_only(monkeypatch):
    config = AppConfig(
        raw={
            "mode": "backtest",
            "logging": {"level": "INFO"},
            "backtest": {"enabled": True},
            "live": {"enabled": True},
        }
    )
    provider = _DummyProvider()
    executed: list[str] = []

    async def fake_backtest(*args, **kwargs):
        executed.append("backtest")

    async def fake_live(*args, **kwargs):
        executed.append("live")

    monkeypatch.setattr("bot.app.load_config", lambda: config)
    monkeypatch.setattr("bot.app.configure_logging", lambda level: None)
    monkeypatch.setattr("bot.app.init_storage", lambda cfg: object())
    monkeypatch.setattr("bot.app.init_providers", lambda cfg: {"dummy": provider})
    monkeypatch.setattr("bot.app.init_services", lambda cfg, storage: tuple(object() for _ in range(7)))
    monkeypatch.setattr("bot.app.build_thresholds", lambda cfg: {})
    monkeypatch.setattr("bot.app.run_backtest", fake_backtest)
    monkeypatch.setattr("bot.app.run_live", fake_live)

    asyncio.run(main_async([]))

    assert executed == ["backtest"]
    assert provider.closed is True


def test_main_async_runs_live_only(monkeypatch):
    config = AppConfig(
        raw={
            "mode": "backtest",
            "logging": {"level": "INFO"},
            "backtest": {"enabled": True},
            "live": {"enabled": True},
        }
    )
    provider = _DummyProvider()
    executed: list[str] = []

    async def fake_backtest(*args, **kwargs):
        executed.append("backtest")

    async def fake_live(*args, **kwargs):
        executed.append("live")

    monkeypatch.setattr("bot.app.load_config", lambda: config)
    monkeypatch.setattr("bot.app.configure_logging", lambda level: None)
    monkeypatch.setattr("bot.app.init_storage", lambda cfg: object())
    monkeypatch.setattr("bot.app.init_providers", lambda cfg: {"dummy": provider})
    monkeypatch.setattr("bot.app.init_services", lambda cfg, storage: tuple(object() for _ in range(7)))
    monkeypatch.setattr("bot.app.build_thresholds", lambda cfg: {})
    monkeypatch.setattr("bot.app.run_backtest", fake_backtest)
    monkeypatch.setattr("bot.app.run_live", fake_live)

    asyncio.run(main_async(["--mode", "live"]))

    assert executed == ["live"]
    assert provider.closed is True


def test_resolve_mode_precedence():
    config = AppConfig(raw={"mode": "live"})
    assert resolve_mode(config, ["--mode", "backtest"]) == "backtest"
    assert resolve_mode(config, []) == "live"


def test_resolve_mode_invalid():
    config = AppConfig(raw={"mode": "demo"})
    with pytest.raises(ValueError):
        resolve_mode(config, [])

    with pytest.raises(ValueError):
        resolve_mode(AppConfig(raw={}), ["--mode", "invalid"])
