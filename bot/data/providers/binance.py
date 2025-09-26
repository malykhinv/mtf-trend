from __future__ import annotations

import hmac
import time
from hashlib import sha256
from typing import Dict, List, Mapping, Protocol, Sequence, cast
from urllib.parse import urlencode

try:
    import httpx  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    httpx = None

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle
from ..models import (
    BinanceBalance,
    BinanceBalancesPayload,
    BinanceBalancesResponse,
    BinanceExchangeInfoPayload,
    BinanceExchangeInfoResponse,
    BinanceKline,
    BinanceKlinesPayload,
    BinanceKlinesResponse,
    BinanceTickers24hPayload,
    BinanceTickers24hResponse,
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


class BinanceFuturesProvider(BaseExchangeProvider):
    exchange = Exchange.BINANCE
    max_ohlcv_limit = 1500
    _ohlcv_limit_fallback = 1500

    def __init__(
        self,
        api_base: str,
        rate_limit_per_minute: int,
        min_quote_volume: float,
        session: _HttpClient | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        super().__init__(api_base, rate_limit_per_minute, min_quote_volume)
        self._session: _HttpClient | None = session or (
            httpx.AsyncClient(timeout=10.0) if httpx else None
        )
        self._api_key = api_key
        self._api_secret = api_secret

    def _require_credentials(self) -> tuple[str, str]:
        if not self._api_key or not self._api_secret:
            raise RuntimeError("Binance API credentials are required for private requests")
        return self._api_key, self._api_secret

    async def _authenticated_get(
        self, endpoint: str, params: Mapping[str, object] | None = None
    ) -> _HttpResponse:
        if not self._session:
            raise RuntimeError("httpx is required to perform authenticated requests to Binance")
        api_key, api_secret = self._require_credentials()
        query_params = dict(params) if params else {}
        query_params.setdefault("timestamp", int(time.time() * 1000))
        query_string = urlencode(query_params)
        signature = hmac.new(api_secret.encode("utf-8"), query_string.encode("utf-8"), sha256).hexdigest()
        query_params["signature"] = signature
        headers = {"X-MBX-APIKEY": api_key}
        return await self._session.get(endpoint, params=query_params, headers=headers)

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        since: int | None = None,
    ) -> List[Candle]:
        await self.rate_limiter.throttle()
        if not self._session:
            raise RuntimeError("httpx is required to fetch candles from Binance")
        limit = self.resolve_ohlcv_limit(limit)
        endpoint = f"{self._api_base}/fapi/v1/klines"
        params = {"symbol": symbol.upper(), "interval": timeframe.value, "limit": limit}
        if since is not None:
            params["startTime"] = since
        response = await self._session.get(endpoint, params=params)
        response.raise_for_status()
        payload = cast(Sequence[Sequence[object]], response.json())
        typed = BinanceKlinesResponse.decode(payload)
        klines_payload = BinanceKlinesPayload.from_http(typed)
        entries: Sequence[BinanceKline] = klines_payload.entries
        candles = self.map_candles(entries, symbol, timeframe)
        filtered = await self._filter_liquidity(candles)
        return self._sort_and_deduplicate(filtered)

    async def get_symbols(self) -> list[str]:
        await self.rate_limiter.throttle()
        if not self._session:
            raise RuntimeError("httpx is required to fetch symbols from Binance")
        endpoint = f"{self._api_base}/fapi/v1/exchangeInfo"
        response = await self._session.get(endpoint)
        response.raise_for_status()
        info = cast(Mapping[str, object], response.json())
        typed = BinanceExchangeInfoResponse.decode(info)
        symbols = BinanceExchangeInfoPayload.from_http(typed).symbols
        trading = [item.symbol for item in symbols if item.status.upper() == "TRADING"]
        return [symbol for symbol in trading if not symbol.endswith("_PERP")]  # filter illiquid synthetics

    async def get_24h_quote_volume(self) -> Dict[str, float]:
        await self.rate_limiter.throttle()
        if not self._session:
            raise RuntimeError("httpx is required to fetch statistics from Binance")
        endpoint = f"{self._api_base}/fapi/v1/ticker/24hr"
        response = await self._session.get(endpoint)
        response.raise_for_status()
        data = cast(Sequence[Mapping[str, object]], response.json())
        typed = BinanceTickers24hResponse.decode(data)
        tickers = BinanceTickers24hPayload.from_http(typed).tickers
        return {ticker.symbol: ticker.quote_volume for ticker in tickers}

    async def update_deposit(self) -> DepositSnapshot:
        await self.rate_limiter.throttle()
        endpoint = f"{self._api_base}/fapi/v2/balance"
        response = await self._authenticated_get(endpoint)
        response.raise_for_status()
        raw_balances = cast(Sequence[Mapping[str, object]], response.json())
        typed = BinanceBalancesResponse.decode(raw_balances)
        balances = BinanceBalancesPayload.from_http(typed).balances

        target_asset = "USDT"
        selected = next((balance for balance in balances if balance.asset == target_asset), None)
        amount = _select_amount(selected)
        raw = selected.raw if selected else None
        return DepositSnapshot(asset=target_asset, balance=amount, raw=raw)

    async def close(self) -> None:
        if self._session:
            await self._session.aclose()


def _select_amount(balance: BinanceBalance | None) -> float:
    if balance is None:
        return 0.0
    amount = _select_first_available(
        (
            balance.available_balance,
            balance.balance,
            balance.cross_wallet_balance,
            balance.equity,
        )
    )
    return amount if amount is not None else 0.0
