"""Utilities for interacting with exchanges via CCXT."""
from __future__ import annotations

from typing import Any

import ccxt

from bot import config
from bot.domain.models.exchange import Exchange


def create_ccxt_client(exchange: Exchange) -> ccxt.Exchange:
    """Build a CCXT client configured for the requested exchange."""

    if exchange is Exchange.BINANCE:
        params: dict[str, Any] = {"enableRateLimit": True}
        if config.BINANCE_API_KEY and config.BINANCE_API_SECRET:
            params.update({"apiKey": config.BINANCE_API_KEY, "secret": config.BINANCE_API_SECRET})
        return ccxt.binanceusdm(params)
    if exchange is Exchange.BYBIT:
        params = {
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "defaultSubType": "linear",
                "defaultSettle": "USDT",
            },
        }
        if config.BYBIT_API_KEY and config.BYBIT_API_SECRET:
            params.update({"apiKey": config.BYBIT_API_KEY, "secret": config.BYBIT_API_SECRET})
        return ccxt.bybit(params)


def fetch_linear_usdt_symbols(exchange: Exchange) -> tuple[str, ...]:
    """Return all linear futures symbols settled in USDT for the exchange."""

    client = create_ccxt_client(exchange)
    markets = client.fetch_markets()
    symbols: set[str] = set()
    for market in markets:
        symbol = market.get("symbol")
        if not symbol:
            continue
        if not symbol.endswith("USDT"):
            continue
        if not market.get("contract", False):
            continue
        if market.get("linear") is False:
            continue
        settle_currency = (market.get("settle") or "").upper()
        quote_currency = (market.get("quote") or "").upper()
        if settle_currency and settle_currency != "USDT":
            continue
        if quote_currency and quote_currency != "USDT":
            continue
        symbols.add(symbol)
    return tuple(sorted(symbols))
