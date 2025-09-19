from __future__ import annotations

from datetime import datetime, timezone

from bot.data.models import (
    BinanceExchangeInfoPayload,
    BinanceExchangeInfoResponse,
    BinanceKlinesPayload,
    BinanceKlinesResponse,
    BinanceTickers24hPayload,
    BinanceTickers24hResponse,
    BybitKlinesPayload,
    BybitKlinesResponse,
    BybitTickersPayload,
    BybitTickersResponse,
)


def test_binance_typed_exchange_info_decoding() -> None:
    response = BinanceExchangeInfoResponse.decode(
        {
            "symbols": [
                {"symbol": "btcusdt", "status": "TRADING"},
                {"symbol": "ethusdt", "status": "BREAK"},
            ]
        }
    )

    payload = BinanceExchangeInfoPayload.from_http(response)

    assert [symbol.symbol for symbol in payload.symbols] == ["BTCUSDT", "ETHUSDT"]


def test_binance_typed_klines_decoding() -> None:
    response = BinanceKlinesResponse.decode(
        [
            [
                1_700_000_000_000,
                "1.0",
                "2.0",
                "0.5",
                "1.5",
                "10",
                1_700_000_059_000,
                "15",
            ]
        ]
    )

    payload = BinanceKlinesPayload.from_http(response)
    entry = payload.entries[0]

    assert entry.open_price == 1.0
    assert entry.volume == 10.0
    assert entry.opened_at == datetime.fromtimestamp(1_700_000_000_000 / 1000, tz=timezone.utc)
    assert entry.quote_volume == 15.0


def test_binance_typed_ticker_decoding() -> None:
    response = BinanceTickers24hResponse.decode(
        [{"symbol": "btcusdt", "quoteVolume": "123.45"}]
    )

    payload = BinanceTickers24hPayload.from_http(response)

    assert [(ticker.symbol, ticker.quote_volume) for ticker in payload.tickers] == [
        ("BTCUSDT", 123.45)
    ]


def test_bybit_typed_klines_decoding() -> None:
    response = BybitKlinesResponse.decode(
        {
            "result": {
                "list": [
                    [
                        1_700_000_000_000,
                        "1.0",
                        "2.0",
                        "0.5",
                        "1.5",
                        "10",
                        "15",
                    ]
                ]
            }
        }
    )

    payload = BybitKlinesPayload.from_http(response)
    entry = payload.entries[0]

    assert entry.close_price == 1.5
    assert entry.volume == 10.0
    assert entry.quote_volume == 15.0


def test_bybit_typed_tickers_decoding() -> None:
    response = BybitTickersResponse.decode(
        {
            "result": {
                "list": [
                    {"symbol": "btcusdt", "turnover24h": "111.1"},
                    {"symbol": "ethusdt", "turnover": "222.2"},
                ]
            }
        }
    )

    payload = BybitTickersPayload.from_http(response)

    assert payload.tickers[0].quote_volume() == 111.1
    assert payload.tickers[1].quote_volume() == 222.2
