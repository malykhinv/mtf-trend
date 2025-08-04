import pytest

from exchanges.binance import BinanceExchange
from exchanges.bybit import BybitExchange


@pytest.mark.asyncio
async def test_binance_rest_snapshot_populates_cache(monkeypatch):
    exch = BinanceExchange("", "")

    snapshots = {
        "perp": {"bids": [["100", "1"]], "asks": [["101", "1"]]},
        "spot": {"bids": [["99", "1"]], "asks": [["102", "1"]]},
    }

    async def fake_request(method, url, **kwargs):
        return snapshots["spot" if "api/v3" in url else "perp"]

    async def dummy_listen(*args, **kwargs):
        return None

    monkeypatch.setattr(exch, "_request", fake_request)
    monkeypatch.setattr(exch, "_listen", dummy_listen)
    monkeypatch.setattr(exch, "_listen_spot", dummy_listen)

    book = await exch.get_orderbook("BTCUSDT", depth=5)
    assert book == snapshots["perp"]
    assert exch._orderbooks["BTCUSDT"] == snapshots["perp"]

    spot_book = await exch.get_spot_orderbook("BTCUSDT", depth=5)
    assert spot_book == snapshots["spot"]
    assert exch._spot_orderbooks["BTCUSDT"] == snapshots["spot"]

    await exch.close()


@pytest.mark.asyncio
async def test_bybit_rest_snapshot_populates_cache(monkeypatch):
    exch = BybitExchange("", "")

    async def fake_request(method, url, **kwargs):
        params = kwargs.get("params", {})
        if params.get("category") == "spot":
            return {"result": {"b": [["200", "1"]], "a": [["201", "1"]]}}
        return {"result": {"b": [["300", "1"]], "a": [["301", "1"]]}}

    async def dummy_listen(*args, **kwargs):
        return None

    monkeypatch.setattr(exch, "_request", fake_request)
    monkeypatch.setattr(exch, "_listen", dummy_listen)
    monkeypatch.setattr(exch, "_listen_spot", dummy_listen)

    book = await exch.get_orderbook("BTCUSDT", depth=5)
    assert book == {"bids": [["300", "1"]], "asks": [["301", "1"]]}
    assert exch._orderbooks["BTCUSDT"] == {"bids": [["300", "1"]], "asks": [["301", "1"]]}

    spot_book = await exch.get_spot_orderbook("BTCUSDT", depth=5)
    assert spot_book == {"bids": [["200", "1"]], "asks": [["201", "1"]]}
    assert exch._spot_orderbooks["BTCUSDT"] == {"bids": [["200", "1"]], "asks": [["201", "1"]]}

    await exch.close()
