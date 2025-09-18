from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Iterable, List

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle
from .base import BaseExchangeProvider


class BybitPerpetualProvider(BaseExchangeProvider):
    exchange = Exchange.BYBIT_PERPETUAL

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

    async def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, limit: int) -> List[Candle]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch candles from Bybit")
        endpoint = f"{self._api_base}/derivatives/v3/public/kline"
        params = {"symbol": symbol.upper(), "interval": timeframe.value, "limit": limit, "category": "linear"}
        response = await self._session.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        raw: Iterable[Any] = data.get("result", {}).get("list", [])
        candles = self.map_candles(raw, symbol, timeframe)
        return await self._filter_liquidity(candles)

    async def stream_candles(
        self, symbol: str, timeframe: Timeframe
    ) -> AsyncIterator[Candle]:
        if not httpx:
            raise RuntimeError("httpx is required for streaming via Bybit API")
        while True:
            candles = await self.fetch_ohlcv(symbol, timeframe, limit=1)
            if candles:
                yield candles[-1]
            await asyncio.sleep(1)

    async def get_symbols(self) -> Iterable[str]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch symbols from Bybit")
        endpoint = f"{self._api_base}/derivatives/v3/public/instruments-info"
        params = {"category": "linear"}
        response = await self._session.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        raw = data.get("result", {}).get("list", [])
        symbols = [item.get("symbol") for item in raw if item.get("status") == "Trading"]
        return [symbol for symbol in symbols if symbol]

    async def update_deposit(self) -> dict[str, Any]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch balances from Bybit")
        endpoint = f"{self._api_base}/v5/account/wallet-balance"
        params = {"accountType": "UNIFIED"}
        response = await self._session.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        balances = data.get("result", {}).get("list", [])
        usdt = next((float(item.get("totalWalletBalance", 0.0)) for item in balances if item.get("coin") == "USDT"), 0.0)
        return {"asset": "USDT", "balance": usdt}

    async def close(self) -> None:
        if self._session:
            await self._session.aclose()
