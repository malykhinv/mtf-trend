from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import ccxt  # type: ignore

from crypto_screener.config.config import cfg
from crypto_screener.data.mappers import map_ohlcv
from crypto_screener.domain.exchange import Exchange, FuturesSymbol
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.utils.extractors import extract_float, extract_int


class Binance(Exchange):
    def __init__(self, api_key: str, api_secret: str):
        self._client = ccxt.binance(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "options": {"defaultType": "swap", "defaultSubType": "linear"},
                "enableRateLimit": True,
            }
        )

    def get_futures_symbols(self) -> Iterable[FuturesSymbol]:
        markets = self._client.load_markets()
        tickers = self._client.fetch_tickers()
        symbols: list[FuturesSymbol] = []

        for market in markets.values():
            if not market.get("contract"):
                continue

            if market.get("settle") != "USDT" and market.get("quote") != "USDT":
                continue

            symbol = market["symbol"]
            ticker = tickers.get(symbol, {})
            info = ticker.get("info", {})
            market_info = market.get("info", {})

            onboard_ms = int(market_info.get("onboardDate", 0) or 0)
            if onboard_ms:
                ts_utc = datetime.fromtimestamp(onboard_ms / 1000, tz=timezone.utc)
            else:
                ts_utc = datetime.fromtimestamp(0, tz=timezone.utc)
            listing_ts = ts_utc.astimezone(cfg.TIMEZONE)

            volume = extract_float(
                ticker.get("quoteVolume"),
                ticker.get("baseVolume"),
                info.get("quoteVolume"),
                info.get("volume"),
            )
            trades = extract_int(
                ticker.get("info", {}).get("count"),
                ticker.get("info", {}).get("trades"),
                info.get("count"),
            )

            symbols.append(
                FuturesSymbol(
                    symbol=symbol,
                    listing_ts=listing_ts,
                    volume_usdt_24h=volume,
                    trades_24h=trades,
                )
            )
        return symbols

    def get_ohlcv(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        raw = self._client.fetch_ohlcv(symbol, timeframe=timeframe.tf, limit=limit)
        return map_ohlcv(raw)

