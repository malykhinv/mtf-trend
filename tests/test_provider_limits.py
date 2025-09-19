import pytest

from bot.data.models import DepositSnapshot
from bot.data.providers.base import BaseExchangeProvider
from bot.domain.enums import Exchange, Timeframe


class _StubProvider(BaseExchangeProvider):
    exchange = Exchange.BINANCE
    max_ohlcv_limit = 1500
    _ohlcv_limit_fallback = 1500

    async def fetch_ohlcv(
        self, symbol: str, timeframe: Timeframe, limit: int, since: int | None = None
    ):  # pragma: no cover - unused
        raise NotImplementedError

    async def stream_candles(self, symbol: str, timeframe: Timeframe):  # pragma: no cover - unused
        raise NotImplementedError

    async def get_symbols(self):  # pragma: no cover - unused
        raise NotImplementedError

    async def get_24h_quote_volume(self):  # pragma: no cover - unused
        raise NotImplementedError

    async def update_deposit(self) -> DepositSnapshot:  # pragma: no cover - unused
        raise NotImplementedError


class _StubBybitProvider(_StubProvider):
    exchange = Exchange.BYBIT
    max_ohlcv_limit = 1000
    _ohlcv_limit_fallback = 1000


@pytest.mark.parametrize(
    "provider_cls,expected_limit,requested_limit",
    [
        (_StubProvider, 1500, None),
        (_StubProvider, 1500, 2000),
        (_StubProvider, 1000, 1000),
        (_StubBybitProvider, 1000, None),
        (_StubBybitProvider, 1000, 1500),
        (_StubBybitProvider, 500, 500),
    ],
)
def test_resolve_ohlcv_limit(provider_cls, expected_limit, requested_limit):
    provider = provider_cls(
        api_base="https://example.com",
        ws_base="wss://example.com/ws",
        rate_limit_per_minute=1200,
        min_quote_volume=1.0,
    )
    assert provider.resolve_ohlcv_limit(requested_limit) == expected_limit
