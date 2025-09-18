from __future__ import annotations

import asyncio
import hmac
import time
from hashlib import sha256
from typing import Any, AsyncIterator, Dict, Iterable, List
from urllib.parse import urlencode

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle
from .base import BaseExchangeProvider


class BybitPerpetualProvider(BaseExchangeProvider):
    exchange = Exchange.BYBIT
    max_ohlcv_limit = 1000
    _ohlcv_limit_fallback = 1000

    def __init__(
        self,
        api_base: str,
        ws_base: str,
        rate_limit_per_minute: int,
        min_quote_volume: float,
        session: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        super().__init__(api_base, ws_base, rate_limit_per_minute, min_quote_volume)
        self._session = session or (httpx.AsyncClient(timeout=10.0) if httpx else None)
        self._api_key = api_key
        self._api_secret = api_secret

    def _require_credentials(self) -> tuple[str, str]:
        if not self._api_key or not self._api_secret:
            raise RuntimeError("Bybit API credentials are required for private requests")
        return self._api_key, self._api_secret

    async def _authenticated_get(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        if not self._session:
            raise RuntimeError("httpx is required to perform authenticated requests to Bybit")
        api_key, api_secret = self._require_credentials()
        query_params = params.copy() if params else {}
        recv_window = str(query_params.pop("recvWindow", query_params.pop("recv_window", "5000")))
        query_string = urlencode(sorted(query_params.items())) if query_params else ""
        timestamp = str(int(time.time() * 1000))
        query_params["recvWindow"] = recv_window
        prehash = f"{timestamp}{api_key}{recv_window}{query_string}"
        signature = hmac.new(api_secret.encode("utf-8"), prehash.encode("utf-8"), sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
        }
        return await self._session.get(endpoint, params=query_params, headers=headers)

    async def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, limit: int) -> List[Candle]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch candles from Bybit")
        limit = self.resolve_ohlcv_limit(limit)
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

    async def get_24h_quote_volume(self) -> Dict[str, float]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch statistics from Bybit")
        endpoint = f"{self._api_base}/derivatives/v3/public/tickers"
        params = {"category": "linear"}
        response = await self._session.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        items = data.get("result", {}).get("list", [])
        volumes: Dict[str, float] = {}
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                symbol = item.get("symbol")
                if not isinstance(symbol, str):
                    continue
                volume_raw = item.get("turnover24h") or item.get("turnover")
                try:
                    volume = float(volume_raw)
                except (TypeError, ValueError):
                    continue
                volumes[symbol.upper()] = volume
        return volumes

    async def update_deposit(self) -> dict[str, Any]:
        await self.ensure_rate_limit()
        endpoint = f"{self._api_base}/v5/account/wallet-balance"
        params = {"accountType": "UNIFIED"}
        response = await self._authenticated_get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        result = data.get("result", {})
        entries = result.get("list", [])
        usdt_balance = 0.0
        for entry in entries:
            coins = entry.get("coin")
            if isinstance(coins, list):
                for coin in coins:
                    if coin.get("coin") == "USDT":
                        for key in (
                            "walletBalance",
                            "availableToWithdraw",
                            "equity",
                            "availableBalance",
                        ):
                            value = coin.get(key)
                            if value is not None:
                                try:
                                    usdt_balance = float(value)
                                    break
                                except (TypeError, ValueError):
                                    continue
                        if usdt_balance:
                            break
            if usdt_balance:
                break
        return {"asset": "USDT", "balance": usdt_balance}

    async def close(self) -> None:
        if self._session:
            await self._session.aclose()
