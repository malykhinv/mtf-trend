import pytest
import sys
import pathlib

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from exchanges.binance import BinanceExchange


class DummyResponse:
    def __init__(self, data):
        self.data = data

    async def json(self):
        return self.data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass


class DummySession:
    @staticmethod
    def get(url):
        if "ticker/24hr" in url:
            return DummyResponse({"quoteVolume": "500", "volume": "5"})
        if "openInterest" in url:
            return DummyResponse({"openInterest": "10"})
        return DummyResponse({})


@pytest.mark.asyncio
async def test_get_stats_uses_quote_volume(monkeypatch):
    ex = BinanceExchange("key", "secret")
    dummy = DummySession()

    async def session_get():
        return dummy

    monkeypatch.setattr(ex, "_session_get", session_get)

    stats = await ex.get_stats("BTCUSDT")
    assert stats["volume_24h"] == 500.0
    assert stats["open_interest"] == 10.0
