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
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        super().__init__(api_base, ws_base, rate_limit_per_minute, min_quote_volume)
        self._session = session or (httpx.AsyncClient(timeout=10.0) if httpx else None)
        self._logger = get_logger(self.__class__.__name__)
        self._api_key = api_key
        self._api_secret = api_secret

    def _require_credentials(self) -> tuple[str, str]:
        if not self._api_key or not self._api_secret:
            raise RuntimeError("Binance API credentials are required for private requests")
        return self._api_key, self._api_secret

    async def _authenticated_get(self, endpoint: str, params: dict[str, Any] | None = None) -> httpx.Response:
        if not self._session:
            raise RuntimeError("httpx is required to perform authenticated requests to Binance")
        api_key, api_secret = self._require_credentials()
        query_params = params.copy() if params else {}
        query_params.setdefault("timestamp", int(time.time() * 1000))
        query_string = urlencode(query_params)
        signature = hmac.new(api_secret.encode("utf-8"), query_string.encode("utf-8"), sha256).hexdigest()
        query_params["signature"] = signature
        headers = {"X-MBX-APIKEY": api_key}
        return await self._session.get(endpoint, params=query_params, headers=headers)

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

    async def get_24h_quote_volume(self) -> Dict[str, float]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch statistics from Binance")
        endpoint = f"{self._api_base}/fapi/v1/ticker/24hr"
        response = await self._session.get(endpoint)
        response.raise_for_status()
        data = response.json()
        volumes: Dict[str, float] = {}
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                symbol = item.get("symbol")
                if not isinstance(symbol, str):
                    continue
                volume_raw = item.get("quoteVolume")
                try:
                    volume = float(volume_raw)
                except (TypeError, ValueError):
                    continue
                volumes[symbol.upper()] = volume
        return volumes

    async def update_deposit(self) -> dict[str, Any]:
        await self.ensure_rate_limit()
        endpoint = f"{self._api_base}/fapi/v2/balance"
        response = await self._authenticated_get(endpoint)
        response.raise_for_status()
        balances = response.json()
        usdt_balance = 0.0
        for item in balances:
            if item.get("asset") != "USDT":
                continue
            for key in ("availableBalance", "balance", "crossWalletBalance", "equity"):
                value = item.get(key)
                if value is not None:
                    try:
                        usdt_balance = float(value)
                        break
                    except (TypeError, ValueError):
                        continue
            if usdt_balance:
                break
        return {"asset": "USDT", "balance": usdt_balance}

    async def close(self) -> None:
        if self._session:
            await self._session.aclose()
