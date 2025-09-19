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
from ..models import BybitInstrument, BybitKline, BybitTicker, DepositSnapshot
from .base import BaseExchangeProvider


@dataclass(slots=True)
class _BybitCoinBalance:
    asset: str
    wallet_balance: float | None
    available_to_withdraw: float | None
    equity: float | None
    available_balance: float | None
    raw: Mapping[str, Any] | None

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "_BybitCoinBalance":
        asset = str(raw.get("coin") or "").upper()
        wallet_balance = _to_float(raw.get("walletBalance"))
        available_to_withdraw = _to_float(raw.get("availableToWithdraw"))
        equity = _to_float(raw.get("equity"))
        available_balance = _to_float(raw.get("availableBalance"))
        return cls(
            asset=asset,
            wallet_balance=wallet_balance,
            available_to_withdraw=available_to_withdraw,
            equity=equity,
            available_balance=available_balance,
            raw=raw,
        )


@dataclass(slots=True)
class _BybitAccountBalance:
    coins: Sequence[_BybitCoinBalance]
    raw: Mapping[str, Any] | None

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "_BybitAccountBalance":
        coins_raw = raw.get("coin")
        coins: list[_BybitCoinBalance] = []
        if isinstance(coins_raw, Sequence):
            for item in coins_raw:
                if isinstance(item, Mapping):
                    coins.append(_BybitCoinBalance.from_raw(item))
        return cls(coins=tuple(coins), raw=raw)


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
        raw_entries = data.get("result", {}).get("list", [])
        if not isinstance(raw_entries, Sequence):
            raise TypeError("Bybit klines payload must be a sequence")
        entries = [BybitKline.from_raw(item) for item in raw_entries]
        candles = self.map_candles(entries, symbol, timeframe)
        filtered = await self._filter_liquidity(candles)
        return self._sort_and_deduplicate(filtered)

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

    async def get_symbols(self) -> list[str]:
        await self.ensure_rate_limit()
        if not self._session:
            raise RuntimeError("httpx is required to fetch symbols from Bybit")
        endpoint = f"{self._api_base}/derivatives/v3/public/instruments-info"
        params = {"category": "linear"}
        response = await self._session.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        instruments = _parse_bybit_instruments(data)
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
        tickers = _parse_bybit_tickers(data)
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
        result_raw = data.get("result")
        entries_raw = result_raw.get("list") if isinstance(result_raw, Mapping) else None
        entries: Sequence[_BybitAccountBalance] = []
        if isinstance(entries_raw, Sequence):
            parsed: list[_BybitAccountBalance] = []
            for entry in entries_raw:
                if isinstance(entry, Mapping):
                    parsed.append(_BybitAccountBalance.from_raw(entry))
            entries = tuple(parsed)

        target_asset = "USDT"
        selected_coin: _BybitCoinBalance | None = None
        raw_coin: Mapping[str, Any] | None = None
        for entry in entries:
            selected_coin = next((coin for coin in entry.coins if coin.asset == target_asset), None)
            if selected_coin:
                raw_coin = selected_coin.raw
                break

        amount = _select_bybit_amount(selected_coin)
        return DepositSnapshot(asset=target_asset, balance=amount, raw=raw_coin)

    async def close(self) -> None:
        if self._session:
            await self._session.aclose()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _select_bybit_amount(balance: _BybitCoinBalance | None) -> float:
    if balance is None:
        return 0.0
    for value in (
        balance.available_to_withdraw,
        balance.available_balance,
        balance.wallet_balance,
        balance.equity,
    ):
        if value is not None:
            return value
    return 0.0


def _parse_bybit_instruments(payload: Any) -> list[BybitInstrument]:
    if not isinstance(payload, Mapping):
        return []
    try:
        result = payload["result"]
    except KeyError:
        return []
    if not isinstance(result, Mapping):
        return []
    try:
        instruments_raw = result["list"]
    except KeyError:
        return []
    if not isinstance(instruments_raw, Sequence):
        return []
    parsed: list[BybitInstrument] = []
    for item in instruments_raw:
        if not isinstance(item, Mapping):
            continue
        try:
            parsed.append(BybitInstrument.from_raw(item))
        except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
            continue
    return parsed


def _parse_bybit_tickers(payload: Any) -> list[BybitTicker]:
    if not isinstance(payload, Mapping):
        return []
    try:
        result = payload["result"]
    except KeyError:
        return []
    if not isinstance(result, Mapping):
        return []
    try:
        tickers_raw = result["list"]
    except KeyError:
        return []
    if not isinstance(tickers_raw, Sequence):
        return []
    parsed: list[BybitTicker] = []
    for item in tickers_raw:
        if not isinstance(item, Mapping):
            continue
        try:
            parsed.append(BybitTicker.from_raw(item))
        except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
            continue
    return parsed
