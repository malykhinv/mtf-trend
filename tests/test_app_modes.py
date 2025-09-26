import asyncio

import pytest

from bot.app import main_async, resolve_mode
from bot.app_modes import AppMode
from bot.data.io.config_loader import AppConfig
from bot.data.io.config_types import parse_app_config_payload


class _DummyProvider:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:  # pragma: no cover - trivial
        self.closed = True


def _make_config(raw: dict[str, object]) -> AppConfig:
    return AppConfig(parse_app_config_payload(raw))


def test_main_async_runs_backtest_only(monkeypatch):
    config = _make_config(
        {
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
    config = _make_config(
        {
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
    config = _make_config({"mode": "live"})
    assert resolve_mode(config, ["--mode", "backtest"]) == AppMode.BACKTEST
    assert resolve_mode(config, []) == AppMode.LIVE


def test_resolve_mode_invalid():
    with pytest.raises(ValueError):
        _make_config({"mode": "demo"})

    config = _make_config({})
    with pytest.raises(ValueError):
        resolve_mode(config, ["--mode", "invalid"])
