import asyncio

from bot.data.providers.binance import BinanceFuturesProvider
from bot.data.providers.bybit import BybitPerpetualProvider
from bot.domain.enums import Timeframe


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:  # pragma: no cover - no errors simulated
        return None

    def json(self):  # pragma: no cover - simple passthrough
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def get(self, endpoint: str, params: dict[str, object] | None = None):
        self.calls.append((endpoint, params))
        return _FakeResponse(self._payload)


def test_binance_fetch_ohlcv_forwards_since_and_sorts() -> None:
    base_ts = 1_700_000_000_000
    payload = [
        [base_ts + 60_000, "1", "2", "0", "1.5", "100", base_ts + 120_000, "200"],
        [base_ts, "1", "2", "0", "1.5", "100", base_ts + 60_000, "150"],
        [base_ts, "1.1", "2.1", "0.1", "1.6", "120", base_ts + 60_000, "160"],
    ]
    session = _FakeSession(payload)
    provider = BinanceFuturesProvider(
        api_base="https://example.com",
        ws_base="wss://example.com/ws",
        rate_limit_per_minute=60,
        min_quote_volume=0.0,
        session=session,
    )

    candles = asyncio.run(
        provider.fetch_ohlcv("BTCUSDT", Timeframe.M1, limit=3, since=1234567890)
    )

    assert session.calls
    _, params = session.calls[0]
    assert params is not None
    assert params.get("startTime") == 1234567890
    assert len(candles) == 2
    assert [c.started_at for c in candles] == sorted(c.started_at for c in candles)


def test_bybit_fetch_ohlcv_forwards_since_and_sorts() -> None:
    base_ts = 1_700_000_000_000
    payload = {
        "result": {
            "list": [
                [
                    base_ts + 60_000,
                    "1",
                    "2",
                    "0",
                    "1.5",
                    "100",
                    "200",
                ],
                [base_ts, "1", "2", "0", "1.5", "100", "150"],
                [base_ts, "1.1", "2.1", "0.1", "1.6", "120", "160"],
            ]
        }
    }
    session = _FakeSession(payload)
    provider = BybitPerpetualProvider(
        api_base="https://example.com",
        ws_base="wss://example.com/ws",
        rate_limit_per_minute=60,
        min_quote_volume=0.0,
        session=session,
    )

    candles = asyncio.run(
        provider.fetch_ohlcv("BTCUSDT", Timeframe.M1, limit=3, since=987654321)
    )

    assert session.calls
    _, params = session.calls[0]
    assert params is not None
    assert params.get("start") == 987654321
    assert len(candles) == 2
    assert [c.started_at for c in candles] == sorted(c.started_at for c in candles)


def test_binance_get_symbols_filters_trading_pairs() -> None:
    payload = {
        "symbols": [
            {"symbol": "btcusdt", "status": "TRADING"},
            {"symbol": "ethusdt", "status": "HALT"},
            {"symbol": "btcusd_perp", "status": "TRADING"},
        ]
    }
    session = _FakeSession(payload)
    provider = BinanceFuturesProvider(
        api_base="https://example.com",
        ws_base="wss://example.com/ws",
        rate_limit_per_minute=60,
        min_quote_volume=0.0,
        session=session,
    )

    symbols = asyncio.run(provider.get_symbols())

    assert symbols == ["BTCUSDT"]


def test_binance_get_24h_quote_volume_uses_typed_payload() -> None:
    payload = [
        {"symbol": "BTCUSDT", "quoteVolume": "123.45"},
        {"symbol": "ETHUSDT", "quoteVolume": 67.89},
    ]
    session = _FakeSession(payload)
    provider = BinanceFuturesProvider(
        api_base="https://example.com",
        ws_base="wss://example.com/ws",
        rate_limit_per_minute=60,
        min_quote_volume=0.0,
        session=session,
    )

    volumes = asyncio.run(provider.get_24h_quote_volume())

    assert volumes == {"BTCUSDT": 123.45, "ETHUSDT": 67.89}


def test_binance_update_deposit_prefers_available_balance() -> None:
    session = _FakeSession([])
    provider = BinanceFuturesProvider(
        api_base="https://example.com",
        ws_base="wss://example.com/ws",
        rate_limit_per_minute=60,
        min_quote_volume=0.0,
        session=session,
    )

    async def fake_auth_get(endpoint: str, params=None):  # type: ignore[override]
        return _FakeResponse(
            [
                {"asset": "BTC", "balance": "10"},
                {
                    "asset": "USDT",
                    "availableBalance": "42.0",
                    "balance": "100.0",
                },
            ]
        )

    provider._authenticated_get = fake_auth_get  # type: ignore[assignment]

    snapshot = asyncio.run(provider.update_deposit())

    assert snapshot.asset == "USDT"
    assert snapshot.balance == 42.0
    assert snapshot.raw == {
        "asset": "USDT",
        "availableBalance": "42.0",
        "balance": "100.0",
    }


def test_bybit_get_symbols_extracts_linear_trading_pairs() -> None:
    payload = {
        "result": {
            "list": [
                {"symbol": "BTCUSDT", "status": "Trading"},
                {"symbol": "ETHUSDT", "status": "Settled"},
            ]
        }
    }
    session = _FakeSession(payload)
    provider = BybitPerpetualProvider(
        api_base="https://example.com",
        ws_base="wss://example.com/ws",
        rate_limit_per_minute=60,
        min_quote_volume=0.0,
        session=session,
    )

    symbols = asyncio.run(provider.get_symbols())

    assert symbols == ["BTCUSDT"]


def test_bybit_get_24h_quote_volume_handles_optional_fields() -> None:
    payload = {
        "result": {
            "list": [
                {"symbol": "BTCUSDT", "turnover24h": "123.45"},
                {"symbol": "ETHUSDT", "turnover": 67.89},
                {"symbol": "XRPUSDT"},
            ]
        }
    }
    session = _FakeSession(payload)
    provider = BybitPerpetualProvider(
        api_base="https://example.com",
        ws_base="wss://example.com/ws",
        rate_limit_per_minute=60,
        min_quote_volume=0.0,
        session=session,
    )

    volumes = asyncio.run(provider.get_24h_quote_volume())

    assert volumes == {"BTCUSDT": 123.45, "ETHUSDT": 67.89}


def test_bybit_update_deposit_prefers_available_to_withdraw() -> None:
    session = _FakeSession({})
    provider = BybitPerpetualProvider(
        api_base="https://example.com",
        ws_base="wss://example.com/ws",
        rate_limit_per_minute=60,
        min_quote_volume=0.0,
        session=session,
    )

    async def fake_auth_get(endpoint: str, params=None):  # type: ignore[override]
        return _FakeResponse(
            {
                "result": {
                    "list": [
                        {
                            "coin": [
                                {"coin": "BTC", "walletBalance": "5"},
                                {
                                    "coin": "USDT",
                                    "availableToWithdraw": "12.5",
                                    "equity": "25",
                                },
                            ]
                        }
                    ]
                }
            }
        )

    provider._authenticated_get = fake_auth_get  # type: ignore[assignment]

    snapshot = asyncio.run(provider.update_deposit())

    assert snapshot.asset == "USDT"
    assert snapshot.balance == 12.5
    assert snapshot.raw == {
        "coin": "USDT",
        "availableToWithdraw": "12.5",
        "equity": "25",
    }
