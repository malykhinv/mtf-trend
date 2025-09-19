from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from bot.app import SymbolUniverse, run_backtest
from bot.data.io.config_loader import AppConfig
from bot.domain.enums import Exchange, Timeframe
from bot.domain.models.entities import Candle, Thresholds


class _StubProvider:
    def __init__(self) -> None:
        self.fetch_calls: list[tuple[str, Timeframe, int, int | None]] = []

    def resolve_ohlcv_limit(self, limit: int | None) -> int:  # pragma: no cover - trivial
        return limit or 5

    async def fetch_ohlcv(
        self, symbol: str, timeframe: Timeframe, limit: int, since: int | None = None
    ):
        self.fetch_calls.append((symbol, timeframe, limit, since))
        return []


def test_run_backtest_iterates_over_timeframes(monkeypatch):
    config = AppConfig(raw={"backtest": {"enabled": True, "history_batches": 1}})
    assert config.backtest.enabled is True
    assert config.backtest.history_batches == 1
    assert config.backtest.timeframes == (
        Timeframe.M1,
        Timeframe.M3,
        Timeframe.M5,
        Timeframe.M15,
    )
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
        return SymbolUniverse(assignments={"BTCUSDT": "dummy"}, pipelines={"dummy": ("BTCUSDT",)})

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
    assert [tf for _, tf, _, _ in provider.fetch_calls] == list(expected_timeframes)


def test_run_backtest_fetches_batched_history(monkeypatch):
    limit = 2
    history_batches = 3
    timeframe = Timeframe.M1
    timeframe_delta = timeframe.to_timedelta()
    now = datetime(2023, 1, 1, 0, 10, tzinfo=timezone.utc)
    start_dt = now - timeframe_delta * limit * history_batches

    def _make_candle(index: int) -> Candle:
        started = start_dt + timeframe_delta * index
        return Candle(
            id=f"candle-{index}",
            symbol="BTCUSDT",
            exchange=Exchange.BINANCE,
            timeframe=timeframe,
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.5 + index,
            volume=1000.0,
            started_at=started,
            closed_at=started + timeframe_delta,
        )

    base_candles = [_make_candle(i) for i in range(4)]
    batches = [
        [base_candles[0], base_candles[1]],
        [base_candles[1], base_candles[2]],
        [base_candles[2], base_candles[3]],
    ]
    data_by_since: dict[int, list[Candle]] = {}
    since_dt = start_dt
    for batch in batches:
        since_value = int(since_dt.timestamp() * 1000)
        data_by_since[since_value] = batch
        since_dt = batch[-1].started_at + timeframe_delta

    class _BatchProvider:
        def __init__(self, mapping: dict[int, list[Candle]]) -> None:
            self._mapping = mapping
            self.fetch_calls: list[tuple[str, Timeframe, int, int | None]] = []

        def resolve_ohlcv_limit(self, requested: int | None) -> int:
            return requested or limit

        async def fetch_ohlcv(
            self, symbol: str, timeframe: Timeframe, limit: int, since: int | None = None
        ) -> list[Candle]:
            self.fetch_calls.append((symbol, timeframe, limit, since))
            return list(self._mapping.get(since or 0, []))

    provider = _BatchProvider(data_by_since)
    config = AppConfig(
        raw={
            "backtest": {
                "enabled": True,
                "limit": limit,
                "history_batches": history_batches,
                "timeframes": [timeframe.value],
            }
        }
    )
    assert config.backtest.limit == limit
    assert config.backtest.history_batches == history_batches
    assert config.backtest.timeframes == (timeframe,)
    thresholds_map = {"default": Thresholds()}
    providers = {"dummy": provider}
    services = tuple(object() for _ in range(7))
    captured: list[list[Candle]] = []

    class _RecordingRunner:
        def __init__(self, *args, **kwargs) -> None:  # pragma: no cover - wiring
            pass

        async def run(self, symbol, candles, thresholds, provider, timeframe=None):
            captured.append(candles)
            return SimpleNamespace(timeframe=timeframe, trades=[], signals=[])

    async def fake_discover(*args, **kwargs):
        return SymbolUniverse(assignments={"BTCUSDT": "dummy"}, pipelines={"dummy": ("BTCUSDT",)})

    async def _exercise() -> None:
        monkeypatch.setattr("bot.app.utcnow", lambda: now)
        monkeypatch.setattr("bot.app.BacktestRunner", _RecordingRunner)
        monkeypatch.setattr("bot.app.discover_symbol_universe", fake_discover)
        monkeypatch.setattr(
            "bot.app.select_provider_for_symbol", lambda *args, **kwargs: provider
        )
        await run_backtest(config, providers, thresholds_map, services)

    asyncio.run(_exercise())

    assert len(provider.fetch_calls) == history_batches
    since_values = [call[3] for call in provider.fetch_calls]
    expected_since = list(data_by_since.keys())
    assert since_values == expected_since
    assert captured
    recorded = captured[0]
    assert [c.id for c in recorded] == [c.id for c in base_candles]
