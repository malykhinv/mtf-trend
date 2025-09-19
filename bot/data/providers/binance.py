from __future__ import annotations

import asyncio
import hmac
import time
from hashlib import sha256
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterable, List, Mapping, Sequence
from urllib.parse import urlencode

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

from ...domain.enums import Exchange, Timeframe
from ...domain.models.entities import Candle
from ...utils.logging import get_logger
from ..models import (
    BinanceKline,
    BinanceSymbolInfo,
    BinanceTicker24h,
    DepositSnapshot,
)
from .base import BaseExchangeProvider


@dataclass(slots=True)
class _BinanceBalance:
    asset: str
    balance: float | None
    available_balance: float | None
    cross_wallet_balance: float | None
    equity: float | None
    raw: Mapping[str, Any] | None

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "_BinanceBalance":
        asset = str(raw.get("asset") or "").upper()
        balance = cls._to_float(raw.get("balance"))
        available_balance = cls._to_float(raw.get("availableBalance"))
        cross_wallet_balance = cls._to_float(raw.get("crossWalletBalance"))
        equity = cls._to_float(raw.get("equity"))
        return cls(
            asset=asset,
            balance=balance,
            available_balance=available_balance,
            cross_wallet_balance=cross_wallet_balance,
            equity=equity,
            raw=raw,
        )

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class BinanceFuturesProvider(BaseExchangeProvider):
    exchange = Exchange.BINANCE
    max_ohlcv_limit = 1500
    _ohlcv_limit_fallback = 1500

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

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        since: int | None = None,
    ) -> List[Candle]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch candles from Binance")
        limit = self.resolve_ohlcv_limit(limit)
        endpoint = f"{self._api_base}/fapi/v1/klines"
        params = {"symbol": symbol.upper(), "interval": timeframe.value, "limit": limit}
        if since is not None:
            params["startTime"] = since
        response = await self._session.get(endpoint, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Sequence):
            raise TypeError("Binance klines payload must be a sequence")
        entries = [BinanceKline.from_raw(item) for item in payload]
        candles = self.map_candles(entries, symbol, timeframe)
        filtered = await self._filter_liquidity(candles)
        return self._sort_and_deduplicate(filtered)

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

    async def get_symbols(self) -> list[str]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch symbols from Binance")
        endpoint = f"{self._api_base}/fapi/v1/exchangeInfo"
        response = await self._session.get(endpoint)
        response.raise_for_status()
        info = response.json()
        symbols = _parse_binance_symbols(info)
        trading = [item.symbol for item in symbols if item.status.upper() == "TRADING"]
        return [symbol for symbol in trading if not symbol.endswith("_PERP")]  # filter illiquid synthetics

    async def get_24h_quote_volume(self) -> Dict[str, float]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch statistics from Binance")
        endpoint = f"{self._api_base}/fapi/v1/ticker/24hr"
        response = await self._session.get(endpoint)
        response.raise_for_status()
        data = response.json()
        tickers = _parse_binance_tickers(data)
        return {ticker.symbol: ticker.quote_volume for ticker in tickers}

    async def update_deposit(self) -> DepositSnapshot:
        await self.ensure_rate_limit()
        endpoint = f"{self._api_base}/fapi/v2/balance"
        response = await self._authenticated_get(endpoint)
        response.raise_for_status()
        raw_balances = response.json()
        balances: Sequence[_BinanceBalance] = []
        if isinstance(raw_balances, Sequence):
            parsed: list[_BinanceBalance] = []
            for item in raw_balances:
                if isinstance(item, Mapping):
                    parsed.append(_BinanceBalance.from_raw(item))
            balances = tuple(parsed)

        target_asset = "USDT"
        selected = next((balance for balance in balances if balance.asset == target_asset), None)
        amount = _select_amount(selected)
        raw = selected.raw if selected else None
        return DepositSnapshot(asset=target_asset, balance=amount, raw=raw)

    async def close(self) -> None:
        if self._session:
            await self._session.aclose()


def _select_amount(balance: _BinanceBalance | None) -> float:
    if balance is None:
        return 0.0
    for value in (
        balance.available_balance,
        balance.balance,
        balance.cross_wallet_balance,
        balance.equity,
    ):
        if value is not None:
            return value
    return 0.0


def _parse_binance_symbols(payload: Any) -> list[BinanceSymbolInfo]:
    if not isinstance(payload, Mapping):
        return []
    try:
        symbols_raw = payload["symbols"]
    except KeyError:
        return []
    if not isinstance(symbols_raw, Sequence):
        return []
    parsed: list[BinanceSymbolInfo] = []
    for item in symbols_raw:
        if not isinstance(item, Mapping):
            continue
        try:
            parsed.append(BinanceSymbolInfo.from_raw(item))
        except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
            continue
    return parsed


def _parse_binance_tickers(payload: Any) -> list[BinanceTicker24h]:
    if not isinstance(payload, Sequence):
        return []
    parsed: list[BinanceTicker24h] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        try:
            parsed.append(BinanceTicker24h.from_raw(item))
        except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
            continue
    return parsed
