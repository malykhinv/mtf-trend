from __future__ import annotations

import asyncio
import hmac
import time
from hashlib import sha256
from typing import AsyncGenerator, Dict, List, Mapping, Protocol, Sequence
from urllib.parse import urlencode

try:
    import httpx  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    httpx = None

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle
from ..models import (
    BybitCoinBalance,
    BybitInstrumentsPayload,
    BybitKline,
    BybitKlinesPayload,
    BybitTicker,
    BybitTickersPayload,
    BybitWalletBalancePayload,
    DepositSnapshot,
)
from ..models.exchange import _select_first_available
from .base import BaseExchangeProvider


class _HttpResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class _HttpClient(Protocol):
    async def get(
        self,
        endpoint: str,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> _HttpResponse: ...

    async def aclose(self) -> None: ...


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
        session: _HttpClient | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        super().__init__(api_base, ws_base, rate_limit_per_minute, min_quote_volume)
        self._session: _HttpClient | None = session or (
            httpx.AsyncClient(timeout=10.0) if httpx else None
        )
        self._api_key = api_key
        self._api_secret = api_secret

    def _require_credentials(self) -> tuple[str, str]:
        if not self._api_key or not self._api_secret:
            raise RuntimeError("Bybit API credentials are required for private requests")
        return self._api_key, self._api_secret

    async def _authenticated_get(
        self, endpoint: str, params: Mapping[str, object] | None = None
    ) -> _HttpResponse:
        if not self._session:
            raise RuntimeError("httpx is required to perform authenticated requests to Bybit")
        api_key, api_secret = self._require_credentials()
        query_params = dict(params) if params else {}
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

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        since: int | None = None,
    ) -> List[Candle]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch candles from Bybit")
        limit = self.resolve_ohlcv_limit(limit)
        endpoint = f"{self._api_base}/derivatives/v3/public/kline"
        params = {
            "symbol": symbol.upper(),
            "interval": timeframe.value,
            "limit": limit,
            "category": "linear",
        }
        if since is not None:
            params["start"] = since
        response = await self._session.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        entries = BybitKlinesPayload.from_http(data).entries
        candles = self.map_candles(entries, symbol, timeframe)
        filtered = await self._filter_liquidity(candles)
        return self._sort_and_deduplicate(filtered)

    async def stream_candles(
        self, symbol: str, timeframe: Timeframe
    ) -> AsyncGenerator[Candle, None]:
        if not httpx:
            raise RuntimeError("httpx is required for streaming via Bybit API")
        while True:
            candles = await self.fetch_ohlcv(symbol, timeframe, limit=1)
            if candles:
                yield candles[-1]
            await asyncio.sleep(1)

    async def get_symbols(self) -> list[str]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch symbols from Bybit")
        endpoint = f"{self._api_base}/derivatives/v3/public/instruments-info"
        params = {"category": "linear"}
        response = await self._session.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        instruments = BybitInstrumentsPayload.from_http(data).instruments
        trading = [item.symbol for item in instruments if item.status == "Trading"]
        return trading

    async def get_24h_quote_volume(self) -> Dict[str, float]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch statistics from Bybit")
        endpoint = f"{self._api_base}/derivatives/v3/public/tickers"
        params = {"category": "linear"}
        response = await self._session.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        tickers = BybitTickersPayload.from_http(data).tickers
        volumes: Dict[str, float] = {}
        for ticker in tickers:
            volume = ticker.quote_volume()
            if volume is None:
                continue
            volumes[ticker.symbol] = volume
        return volumes

    async def update_deposit(self) -> DepositSnapshot:
        await self.ensure_rate_limit()
        endpoint = f"{self._api_base}/v5/account/wallet-balance"
        params = {"accountType": "UNIFIED"}
        response = await self._authenticated_get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        accounts = BybitWalletBalancePayload.from_http(data).accounts

        target_asset = "USDT"
        selected_coin: BybitCoinBalance | None = None
        raw_coin: Mapping[str, object] | None = None
        for entry in accounts:
            selected_coin = next((coin for coin in entry.coins if coin.asset == target_asset), None)
            if selected_coin:
                raw_coin = selected_coin.raw
                break

        amount = _select_bybit_amount(selected_coin)
        return DepositSnapshot(asset=target_asset, balance=amount, raw=raw_coin)

    async def close(self) -> None:
        if self._session:
            await self._session.aclose()


def _select_bybit_amount(balance: BybitCoinBalance | None) -> float:
    if balance is None:
        return 0.0
    amount = _select_first_available(
        (
            balance.available_to_withdraw,
            balance.available_balance,
            balance.wallet_balance,
            balance.equity,
        )
    )
    return amount if amount is not None else 0.0
