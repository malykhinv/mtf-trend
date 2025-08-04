import pytest
from decimal import Decimal

from strategies import funding_arbitrage as fa


@pytest.mark.asyncio
async def test_get_market_metrics_missing_fields(monkeypatch):
    async def fake_fetch_funding_history(symbol, hours=8, limit=3):
        return [0.01]

    async def fake_get_orderbook(symbol, depth=5):
        return {"bids": [], "asks": [(100, 1)]}

    async def fake_get_spot_orderbook(symbol, depth=5):
        return {"bids": [(99, 1)], "asks": [(101, 1)]}

    async def fake_get_stats(symbol):
        return {"volume_24h": 0, "open_interest": 0}

    async def fake_get_ohlc(symbol, interval="15m", limit=1):
        return [{"open": 100, "close": 100}]

    monkeypatch.setattr(fa, "fetch_funding_history", fake_fetch_funding_history)
    monkeypatch.setattr(fa, "get_orderbook", fake_get_orderbook)
    monkeypatch.setattr(fa, "get_spot_orderbook", fake_get_spot_orderbook)
    monkeypatch.setattr(fa, "get_stats", fake_get_stats)
    monkeypatch.setattr(fa, "get_ohlc", fake_get_ohlc)

    with pytest.raises(fa.MissingMetricsError) as excinfo:
        await fa.get_market_metrics("BTCUSDT", Decimal("1"))

    assert "perp_bids" in excinfo.value.missing_fields
