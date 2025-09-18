from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Iterable, List

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle
from ...utils.logging import get_logger
from .base import BaseExchangeProvider


class BinanceFuturesProvider(BaseExchangeProvider):
    exchange = Exchange.BINANCE

    def __init__(
        self,
        api_base: str,
        ws_base: str,
        rate_limit_per_minute: int,
        min_quote_volume: float,
        session: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_base, ws_base, rate_limit_per_minute, min_quote_volume)
        self._session = session or (httpx.AsyncClient(timeout=10.0) if httpx else None)
        self._logger = get_logger(self.__class__.__name__)

    async def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, limit: int) -> List[Candle]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch candles from Binance")
        endpoint = f"{self._api_base}/fapi/v1/klines"
        params = {"symbol": symbol.upper(), "interval": timeframe.value, "limit": limit}
        response = await self._session.get(endpoint, params=params)
        response.raise_for_status()
        raw: Iterable[Any] = response.json()
        candles = self.map_candles(raw, symbol, timeframe)
        return await self._filter_liquidity(candles)

    async def stream_candles(
        self, symbol: str, timeframe: Timeframe
    ) -> AsyncIterator[Candle]:
        if not httpx:
            raise RuntimeError("httpx is required for streaming via Binance API")
        # Binance delivers partial candles via websocket; we approximate with polling for simplicity
        while True:
            candles = await self.fetch_ohlcv(symbol, timeframe, limit=1)
            if candles:
                yield candles[-1]
            await asyncio.sleep(1)

    async def get_symbols(self) -> Iterable[str]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch symbols from Binance")
        endpoint = f"{self._api_base}/fapi/v1/exchangeInfo"
        response = await self._session.get(endpoint)
        response.raise_for_status()
        info = response.json()
        symbols = [item["symbol"] for item in info.get("symbols", []) if item.get("status") == "TRADING"]
        return [symbol for symbol in symbols if not symbol.endswith("_PERP")]  # filter illiquid synthetics

    async def update_deposit(self) -> dict[str, Any]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch account balance from Binance")
        endpoint = f"{self._api_base}/fapi/v2/balance"
        response = await self._session.get(endpoint)
        response.raise_for_status()
        balances = response.json()
        usdt_balance = next((float(item.get("balance", 0.0)) for item in balances if item.get("asset") == "USDT"), 0.0)
        return {"asset": "USDT", "balance": usdt_balance}

    async def close(self) -> None:
        if self._session:
            await self._session.aclose()
