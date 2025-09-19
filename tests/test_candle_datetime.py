from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.data.mappers.ohlcv_mapper import map_ohlcv
from bot.data.providers.base import BaseExchangeProvider
from bot.domain.enums import Exchange, Timeframe
from bot.domain.models.entities import Candle


class _DummyProvider(BaseExchangeProvider):
    exchange = Exchange.BINANCE

    async def fetch_ohlcv(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def _build_candle(started_at: datetime) -> Candle:
    return Candle(
        id="c1",
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        timeframe=Timeframe.M1,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
        started_at=started_at,
        closed_at=started_at,
    )


def test_map_ohlcv_started_at_is_datetime() -> None:
    raw = [1_697_049_600_000, 1, 2, 3, 4, 5, 6, 7]
    candle = map_ohlcv(raw, "BTC/USDT", Exchange.BINANCE, Timeframe.M1)

    assert isinstance(candle.started_at, datetime)
    assert candle.started_at.tzinfo is not None


def test_map_ohlcv_rejects_naive_datetime() -> None:
    raw = [datetime(2023, 1, 1), 1, 2, 3, 4, 5]

    with pytest.raises(ValueError):
        map_ohlcv(raw, "BTC/USDT", Exchange.BINANCE, Timeframe.M1)


def test_map_ohlcv_accepts_aware_datetime() -> None:
    aware_datetime = datetime(2023, 1, 1, tzinfo=timezone.utc)
    raw = [aware_datetime, 1, 2, 3, 4, 5]

    candle = map_ohlcv(raw, "BTC/USDT", Exchange.BINANCE, Timeframe.M1)

    assert candle.started_at.tzinfo is not None
    assert candle.started_at.tzinfo.utcoffset(candle.started_at) == timezone.utc.utcoffset(aware_datetime)


def test_sort_and_deduplicate_requires_datetime_started_at() -> None:
    provider = _DummyProvider("api", "ws", rate_limit_per_minute=10, min_quote_volume=0.0)
    valid_started_at = datetime(2023, 1, 1, tzinfo=timezone.utc)
    valid_candle = _build_candle(valid_started_at)
    invalid_candle = Candle(
        id="c2",
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        timeframe=Timeframe.M1,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
        started_at=0,  # type: ignore[arg-type]
        closed_at=valid_started_at,
    )

    with pytest.raises(TypeError):
        provider._sort_and_deduplicate([valid_candle, invalid_candle])
