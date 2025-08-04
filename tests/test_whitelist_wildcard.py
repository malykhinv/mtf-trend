import pytest
import main


@pytest.mark.asyncio
async def test_wildcard_whitelist_populates_symbols(monkeypatch):
    class DummyExchange:
        name = "binance"

        def __init__(self, key, secret):
            pass

        async def get_futures_symbols(self):
            return ["BTCUSDT", "ETHUSDT"]

        async def get_spot_symbols(self):
            return ["BTCUSDT", "ETHUSDT", "XRPUSDT"]

        async def close(self):
            pass

    monkeypatch.setattr(main, "BinanceExchange", DummyExchange)
    monkeypatch.setattr(main.risk_control, "configure", lambda cfg, dep: None)

    main.CONFIG = {
        "api_keys": {"binance": "k", "binance_secret": "s"},
        "bot": {"whitelist": "*", "deposit_size": 100},
        "risk": {},
    }
    main.CLIENTS.clear()
    main.WHITELISTS.clear()

    await main.initialize_bot()

    assert main.WHITELISTS["binance"] == ["BTCUSDT", "ETHUSDT"]

    # cleanup
    main.CLIENTS.clear()
    main.WHITELISTS.clear()
