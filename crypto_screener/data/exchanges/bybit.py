from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

import ccxt  # type: ignore

from crypto_screener.data.mappers import map_ohlcv
from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.margin_mode import MarginMode
from crypto_screener.domain.models.order_info import OrderInfo
from crypto_screener.domain.models.order_side import OrderSide
from crypto_screener.domain.models.position import Position
from crypto_screener.domain.models.symbol import FuturesSymbol
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.utils.extractors import extract_float, extract_int


class Bybit(Exchange):
    def __init__(
            self,
            api_key: str,
            api_secret: str
    ) -> None:
        self._client = ccxt.bybit(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "options": {"defaultType": "future"},
                "enableRateLimit": True,
            }
        )

    def get_futures_symbols(self) -> Iterable[FuturesSymbol]:
        markets = self._client.load_markets()
        tickers = self._client.fetch_tickers()
        symbols: list[FuturesSymbol] = []

        for market in markets.values():
            if not market.get("linear"):
                continue

            symbol = market["symbol"]
            ticker = tickers.get(symbol, {})
            info = ticker.get("info", {})
            market_info = market.get("info", {})

            launch_time = market_info.get("launchTime") or market_info.get("createdAt")
            onboard_ms = int(launch_time) if launch_time else 0
            if onboard_ms:
                listing_time = datetime.fromtimestamp(onboard_ms / 1000, tz=timezone.utc)
            else:
                listing_time = datetime.fromtimestamp(0, tz=timezone.utc)

            volume = extract_float(
                ticker.get("quoteVolume"),
                ticker.get("baseVolume"),
                info.get("turnover24h"),
                info.get("volume24h"),
            )
            trades = extract_int(
                info.get("tradeCount"),
                ticker.get("info", {}).get("tradeCount"),
            )

            symbols.append(
                FuturesSymbol(
                    symbol=symbol,
                    listing_time=listing_time,
                    volume_usdt_24h=volume,
                    trades_24h=trades,
                )
            )
        return symbols

    # noinspection DuplicatedCode
    def get_ohlcv(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int,
            end: Optional[datetime] = None
    ) -> list[Bar]:
        params: dict[str, int] = {"limit": limit}
        if end:
            timeframe_ms = timeframe.minutes * 60 * 1000
            end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
            params["since"] = end_ms - timeframe_ms * limit

        raw = self._client.fetch_ohlcv(
            symbol=symbol,
            timeframe=timeframe.tf,
            since=params.get("since"),
            limit=params["limit"],
        )
        return map_ohlcv(raw, end)

    def place_market_order(
            self,
            symbol: str,
            side: OrderSide,
            quantity: float,
            margin_mode: Optional[MarginMode] = None,
    ) -> str:
        raise NotImplementedError("Market orders are not implemented for Bybit yet")

    def cancel_order(self, symbol: str, order_id: str) -> None:
        raise NotImplementedError("Order cancellation is not implemented for Bybit yet")

    def get_order_status(self, symbol: str, order_id: str) -> OrderInfo:
        raise NotImplementedError("Order status retrieval is not implemented for Bybit yet")

    def get_position(self, symbol: str) -> Optional[Position]:
        raise NotImplementedError("Position retrieval is not implemented for Bybit yet")

    def place_stop_loss_order(
            self,
            symbol: str,
            side: OrderSide,
            quantity: float,
            stop_price: float,
            reduce_only: bool = True,
            margin_mode: Optional[MarginMode] = None,
    ) -> str:
        raise NotImplementedError("Stop-loss orders are not implemented for Bybit yet")

    def place_take_profit_order(
            self,
            symbol: str,
            side: OrderSide,
            quantity: float,
            price: float,
            reduce_only: bool = True,
            margin_mode: Optional[MarginMode] = None,
    ) -> str:
        raise NotImplementedError("Take-profit orders are not implemented for Bybit yet")
