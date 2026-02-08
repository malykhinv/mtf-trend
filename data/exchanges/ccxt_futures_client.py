"""CCXT-based futures-only exchange adapter."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, TypedDict, cast, runtime_checkable

import pandas as pd

from constants import (
    CCXT_MARKET_TYPE_SWAP,
    DEFAULT_FETCH_BATCH_SIZE,
    EXCHANGE_TIMEOUT_SECONDS,
    FUTURES_SETTLEMENT_QUOTE_ASSET,
    MILLISECONDS_IN_SECOND,
    OHLCV_FRAME_COLUMNS,
    OPEN_INTEREST_FRAME_COLUMNS,
)
from domain.abstract.exchange_client import ExchangeClient
from domain.enums.exchange import Exchange
from domain.enums.timeframe import Timeframe
from utils.retry import RetryExhaustedError, run_with_retry

try:
    import ccxt  # type: ignore
except ImportError:  # pragma: no cover
    ccxt = None


class CcxtClientOptions(TypedDict):
    defaultType: str


class CcxtFuturesApi(Protocol):
    markets: dict[str, dict[str, object]]

    def load_markets(self) -> object:
        ...

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        ...


@runtime_checkable
class CcxtOpenInterestApi(Protocol):
    def fetch_open_interest_history(
        self,
        symbol: str,
        timeframe: str,
        since: int,
        limit: int,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        ...

# region Private

def _to_utc_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return int(value.timestamp() * MILLISECONDS_IN_SECOND)

# endregion Private


class CcxtFuturesClient(ExchangeClient):
    """Futures-only implementation for Binance/Bybit/OKX via CCXT."""

    def __init__(
        self,
        exchange: Exchange | str,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        enable_rate_limit: bool = True,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        if ccxt is None:
            raise RuntimeError("ccxt is required for CcxtFuturesClient")

        self.exchange = Exchange(str(exchange).upper())
        self._logger = logging.getLogger(self.__class__.__name__)
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._client: CcxtFuturesApi = self._build_client(
            exchange=self.exchange,
            api_key=api_key,
            secret=secret,
            password=password,
            enable_rate_limit=enable_rate_limit,
        )
        self._client.load_markets()

    # region Private

    @staticmethod
    def _build_client(
        exchange: Exchange,
        api_key: str,
        secret: str,
        password: str,
        enable_rate_limit: bool,
    ) -> CcxtFuturesApi:
        params: dict[str, object] = {
            "apiKey": api_key,
            "secret": secret,
            "password": password,
            "enableRateLimit": enable_rate_limit,
            "timeout": EXCHANGE_TIMEOUT_SECONDS * MILLISECONDS_IN_SECOND,
        }
        if exchange == Exchange.BINANCE:
            return ccxt.binanceusdm(cast(Any, params))
        if exchange == Exchange.BYBIT:
            params["options"] = CcxtClientOptions(defaultType=CCXT_MARKET_TYPE_SWAP)
            return ccxt.bybit(cast(Any, params))
        if exchange == Exchange.OKX:
            params["options"] = CcxtClientOptions(defaultType=CCXT_MARKET_TYPE_SWAP)
            return ccxt.okx(cast(Any, params))

    # endregion Private

    def _retry_exchange_call(
        self,
        operation: str,
        symbol: str,
        endpoint: str,
        call: Callable[..., object],
        **kwargs: object,
    ) -> object:
        try:
            return run_with_retry(
                operation=operation,
                call=call,
                attempts=self._retry_attempts,
                backoff_seconds=self._retry_backoff_seconds,
                retriable_exceptions=(Exception,),
                logger=self._logger,
                endpoint=endpoint,
                symbol=symbol,
                jitter_seconds=0.25,
                **kwargs,
            )
        except RetryExhaustedError as exc:
            raise RuntimeError(
                f"Exchange retry exhausted: operation={operation} symbol={symbol} endpoint={endpoint} attempts={self._retry_attempts}"
            ) from exc

    def get_futures_symbols(self) -> list[str]:
        symbols: list[str] = []
        for market in self._client.markets.values():
            if not market.get("active", True):
                continue
            if not (market.get("swap") or market.get("future")):
                continue

            quote = str(market.get("quote") or "").upper()
            settle = str(market.get("settle") or "").upper()
            linear = bool(market.get("linear", False))

            if quote != FUTURES_SETTLEMENT_QUOTE_ASSET and settle != FUTURES_SETTLEMENT_QUOTE_ASSET:
                continue
            if not linear and settle != FUTURES_SETTLEMENT_QUOTE_ASSET:
                continue

            symbol = str(market.get("symbol") or "")
            if symbol:
                symbols.append(symbol)

        return sorted(set(symbols))

    def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, start_time: datetime, end_time: datetime) -> pd.DataFrame:
        start_ms = _to_utc_ms(start_time)
        since = start_ms
        end_ms = _to_utc_ms(end_time)

        all_rows: list[list[float]] = []
        while since <= end_ms:
            batch = self._retry_exchange_call(
                operation="ccxt_fetch_ohlcv",
                symbol=symbol,
                endpoint="fetch_ohlcv",
                call=self._client.fetch_ohlcv,
                timeframe=timeframe,
                since=since,
                limit=DEFAULT_FETCH_BATCH_SIZE,
            )
            if not isinstance(batch, list):
                break
            if not batch:
                break
            all_rows.extend(batch)
            last_ts = int(batch[-1][0])
            if last_ts >= end_ms:
                break
            since = last_ts + 1

        frame = pd.DataFrame(all_rows, columns=OHLCV_FRAME_COLUMNS)
        if frame.empty:
            return frame

        frame = frame.loc[(frame["timestamp"] >= start_ms) & (frame["timestamp"] <= end_ms)]
        frame["datetime"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        return frame.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    def fetch_open_interest(self, symbol: str, timeframe: Timeframe, start_time: datetime, end_time: datetime) -> pd.DataFrame:
        if not isinstance(self._client, CcxtOpenInterestApi):
            raise NotImplementedError(f"Exchange {self.exchange.value} does not support fetch_open_interest_history in CCXT")

        start_ms = _to_utc_ms(start_time)
        since = start_ms
        end_ms = _to_utc_ms(end_time)

        rows: list[dict[str, object]] = []
        while since <= end_ms:
            try:
                batch = self._retry_exchange_call(
                    operation="ccxt_fetch_open_interest_history",
                    symbol=symbol,
                    endpoint="fetch_open_interest_history",
                    call=self._client.fetch_open_interest_history,
                    timeframe=timeframe,
                    since=since,
                    limit=DEFAULT_FETCH_BATCH_SIZE,
                    params={"intervalTime": timeframe, "period": timeframe},
                )
            except TypeError:
                batch = self._retry_exchange_call(
                    operation="ccxt_fetch_open_interest_history",
                    symbol=symbol,
                    endpoint="fetch_open_interest_history",
                    call=self._client.fetch_open_interest_history,
                    timeframe=timeframe,
                    since=since,
                    limit=DEFAULT_FETCH_BATCH_SIZE,
                )
            if not isinstance(batch, list):
                break
            if not batch:
                break

            rows.extend(batch)
            last_ts = int(batch[-1].get("timestamp") or 0)
            if last_ts >= end_ms:
                break
            since = last_ts + 1

        if not rows:
            return pd.DataFrame(columns=OPEN_INTEREST_FRAME_COLUMNS)

        frame = pd.DataFrame(rows)
        if "openInterestAmount" in frame.columns:
            frame["open_interest"] = pd.to_numeric(frame["openInterestAmount"], errors="coerce")
        elif "openInterestValue" in frame.columns:
            frame["open_interest"] = pd.to_numeric(frame["openInterestValue"], errors="coerce")
        else:
            frame["open_interest"] = pd.to_numeric(frame.get("openInterest"), errors="coerce")

        frame = frame.loc[(frame["timestamp"] >= start_ms) & (frame["timestamp"] <= end_ms)]
        frame["datetime"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        frame = frame[list(OPEN_INTEREST_FRAME_COLUMNS)]
        return frame.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
