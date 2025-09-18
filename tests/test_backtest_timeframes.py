from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bot.app import run_backtest
from bot.data.io.config_loader import AppConfig
from bot.domain.enums import Timeframe
from bot.domain.models.entities import Thresholds


class _StubProvider:
    def __init__(self) -> None:
        self.fetch_calls: list[tuple[str, Timeframe, int | None]] = []

    def resolve_ohlcv_limit(self, limit: int | None) -> int | None:  # pragma: no cover - trivial
        return limit

    async def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, limit: int | None):
        self.fetch_calls.append((symbol, timeframe, limit))
        return []


def test_run_backtest_iterates_over_timeframes(monkeypatch):
    config = AppConfig(raw={"backtest": {"enabled": True}})
    provider = _StubProvider()
    providers = {"dummy": provider}
    thresholds_map = {"default": Thresholds()}
    services = tuple(object() for _ in range(7))
    run_calls: list[tuple[str, Timeframe | None]] = []

    class _StubRunner:
        def __init__(self, *args, **kwargs) -> None:  # pragma: no cover - simple wiring
            pass

        async def run(self, symbol, candles, thresholds, provider, timeframe=None):
            run_calls.append((symbol, timeframe))
            return SimpleNamespace(timeframe=timeframe, trades=[], signals=[])

    async def fake_discover(*args, **kwargs):
        return {"BTCUSDT": "dummy"}

    async def _exercise() -> None:
        monkeypatch.setattr("bot.app.BacktestRunner", _StubRunner)
        monkeypatch.setattr("bot.app.discover_symbol_universe", fake_discover)
        monkeypatch.setattr(
            "bot.app.select_provider_for_symbol", lambda *args, **kwargs: provider
        )
        await run_backtest(config, providers, thresholds_map, services)

    asyncio.run(_exercise())

    expected_timeframes = (Timeframe.M1, Timeframe.M3, Timeframe.M5, Timeframe.M15)
    assert run_calls == [("BTCUSDT", timeframe) for timeframe in expected_timeframes]
    assert [tf for _, tf, _ in provider.fetch_calls] == list(expected_timeframes)
