import sys
from pathlib import Path
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from exchanges.binance import BinanceExchange


@pytest.mark.asyncio
async def test_get_order_status_clears_cache(monkeypatch):
    exchange = BinanceExchange(api_key="key", api_secret="secret")
    exchange._order_symbols["1"] = "BTCUSDT"

    async def fake_request(method, url, retries=3, **kwargs):
        return {"status": "FILLED"}

    monkeypatch.setattr(exchange, "_request", fake_request)

    result = await exchange.get_order_status("1")
    assert result["status"] == "FILLED"
    assert exchange._order_symbols == {}
