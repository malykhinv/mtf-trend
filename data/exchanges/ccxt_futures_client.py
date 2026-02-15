"""Модуль проекта."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, cast

import pandas as pd

from constants import (
    CCXT_MARKET_TYPE_SWAP,
    DEFAULT_FETCH_BATCH_SIZE,
    EXCHANGE_TIMEOUT_SECONDS,
    FUTURES_SETTLEMENT_QUOTE_ASSET,
    MILLISECONDS_IN_SECOND,
    OHLCV_FRAME_COLUMNS,
    OPEN_INTEREST_FRAME_COLUMNS,
    TIMEFRAME_TO_DELTA,
)
from data.exchanges.ccxt_types import CcxtClientOptions, CcxtFuturesApi, CcxtOpenInterestApi
from domain.abstract.exchange_client import ExchangeClient
from domain.exceptions import ExchangeConnectivityError
from domain.enums.exchange import Exchange
from domain.enums.timeframe import Timeframe
from utils.retry import RetryExhaustedError, run_with_retry

try:
    import ccxt  # type: ignore
except ImportError:  # pragma: no cover
    ccxt = None


class CcxtFuturesClient(ExchangeClient):
    """Класс."""
    def __init__(
        self,
        exchange: Exchange,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        enable_rate_limit: bool = True,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        if ccxt is None:
            raise RuntimeError("ccxt is required for CcxtFuturesClient")

        self.exchange = exchange
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
        self._markets_loaded = False

    # region Приватные

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
            params["options"] = CcxtClientOptions(
                defaultType=CCXT_MARKET_TYPE_SWAP,
                fetchCurrencies=False,
            )
            return ccxt.binanceusdm(cast(Any, params))
        if exchange == Exchange.BYBIT:
            params["options"] = CcxtClientOptions(defaultType=CCXT_MARKET_TYPE_SWAP)
            return ccxt.bybit(cast(Any, params))
        if exchange == Exchange.OKX:
            params["options"] = CcxtClientOptions(defaultType=CCXT_MARKET_TYPE_SWAP)
            return ccxt.okx(cast(Any, params))

    def _retry_exchange_call(
        self,
        operation: str,
        symbol: str,
        endpoint: str,
        call: Callable[..., object],
        args: tuple[object, ...] = (),
        **kwargs: object,
    ) -> object:
        try:
            return run_with_retry(
                operation,
                call,
                *args,
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

    def _retry_exchange_startup_call(
        self,
        operation: str,
        endpoint: str,
        call: Callable[..., object],
        args: tuple[object, ...] = (),
        **kwargs: object,
    ) -> object:
        try:
            return run_with_retry(
                operation,
                call,
                *args,
                attempts=self._retry_attempts,
                backoff_seconds=self._retry_backoff_seconds,
                retriable_exceptions=(Exception,),
                logger=self._logger,
                endpoint=endpoint,
                jitter_seconds=0.25,
                **kwargs,
            )
        except RetryExhaustedError as exc:
            raise ExchangeConnectivityError(
                "Не удалось загрузить рынки биржи после повторных попыток. "
                "Вероятная причина: сетевая недоступность или DNS-сбой при обращении к API биржи. "
                "Проверьте DNS-резолвинг, настройки прокси и правила firewall. "
                f"exchange={self.exchange.value} endpoint={endpoint} attempts={self._retry_attempts}"
            ) from exc

    def _ensure_markets_loaded(self) -> None:
        if self._markets_loaded:
            return
        self._retry_exchange_startup_call(
            operation="ccxt_load_markets",
            endpoint="load_markets",
            call=self._client.load_markets,
        )
        self._markets_loaded = True

    # endregion Приватные

    def get_futures_symbols(self) -> list[str]:
        """Возвращает список доступных фьючерсных символов."""
        self._ensure_markets_loaded()
        symbols: list[str] = []
        for market in self._client.markets.values():
            if not market.get("active", True):
                continue
            if not market.get("swap"):
                continue

            quote = str(market.get("quote") or "").upper()
            settle = str(market.get("settle") or "").upper()
            linear = bool(market.get("linear", False))

            if quote != FUTURES_SETTLEMENT_QUOTE_ASSET and settle != FUTURES_SETTLEMENT_QUOTE_ASSET:
                continue
            if not linear and settle != FUTURES_SETTLEMENT_QUOTE_ASSET:
                continue

            symbol = str(market.get("symbol") or "")
            if symbol and not re.search(r"-\d{4,}$", symbol):
                symbols.append(symbol)

        return sorted(set(symbols))

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        """Запрашивает свечи по символу и интервалу."""
        self._ensure_markets_loaded()
        since = start_timestamp_ms

        all_rows: list[list[float]] = []
        while since <= end_timestamp_ms:
            batch = self._retry_exchange_call(
                operation="ccxt_fetch_ohlcv",
                symbol=symbol,
                endpoint="fetch_ohlcv",
                call=self._client.fetch_ohlcv,
                args=(symbol,),
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
            if last_ts >= end_timestamp_ms:
                break
            since = last_ts + 1

        frame = pd.DataFrame(all_rows, columns=OHLCV_FRAME_COLUMNS)
        if frame.empty:
            return frame

        frame = frame.loc[
            (frame["timestamp"] >= start_timestamp_ms)
            & (frame["timestamp"] <= end_timestamp_ms)
        ]
        return frame.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    def fetch_open_interest(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_timestamp_ms: int,
        end_timestamp_ms: int,
    ) -> pd.DataFrame:
        """Запрашивает историю open interest по символу."""
        self._ensure_markets_loaded()
        if not isinstance(self._client, CcxtOpenInterestApi):
            raise NotImplementedError(f"Exchange {self.exchange.value} does not support fetch_open_interest_history in CCXT")

        since = start_timestamp_ms
        ccxt_timeframe = timeframe.value
        timeframe_ms = int(TIMEFRAME_TO_DELTA[timeframe].total_seconds() * MILLISECONDS_IN_SECOND)
        oi_limit = min(DEFAULT_FETCH_BATCH_SIZE, 500) if self.exchange == Exchange.BINANCE else DEFAULT_FETCH_BATCH_SIZE

        rows: list[dict[str, object]] = []
        while since <= end_timestamp_ms:
            request_end_ms = min(end_timestamp_ms, since + timeframe_ms * oi_limit - 1)
            params: dict[str, object] | None = None
            if self.exchange == Exchange.BINANCE:
                params = {
                    "period": ccxt_timeframe,
                    "endTime": request_end_ms,
                }

            try:
                batch = self._retry_exchange_call(
                    operation="ccxt_fetch_open_interest_history",
                    symbol=symbol,
                    endpoint="fetch_open_interest_history",
                    call=self._client.fetch_open_interest_history,
                    args=(symbol,),
                    timeframe=ccxt_timeframe,
                    since=since,
                    limit=oi_limit,
                    params=params,
                )
            except TypeError:
                batch = self._retry_exchange_call(
                    operation="ccxt_fetch_open_interest_history",
                    symbol=symbol,
                    endpoint="fetch_open_interest_history",
                    call=self._client.fetch_open_interest_history,
                    args=(symbol,),
                    timeframe=ccxt_timeframe,
                    since=since,
                    limit=oi_limit,
                )
            if not isinstance(batch, list):
                break
            if not batch:
                break

            rows.extend(batch)
            last_ts = int(batch[-1].get("timestamp") or 0)
            if last_ts >= end_timestamp_ms:
                break
            if last_ts < since:
                since = request_end_ms + 1
                continue
            since = max(last_ts + 1, request_end_ms + 1)

        if not rows:
            return pd.DataFrame(columns=OPEN_INTEREST_FRAME_COLUMNS)

        frame = pd.DataFrame(rows)
        if "openInterestAmount" in frame.columns:
            frame["open_interest"] = pd.to_numeric(frame["openInterestAmount"], errors="coerce")
        elif "openInterestValue" in frame.columns:
            frame["open_interest"] = pd.to_numeric(frame["openInterestValue"], errors="coerce")
        else:
            frame["open_interest"] = pd.to_numeric(frame.get("openInterest"), errors="coerce")

        frame = frame.loc[
            (frame["timestamp"] >= start_timestamp_ms)
            & (frame["timestamp"] <= end_timestamp_ms)
        ]
        frame = frame[list(OPEN_INTEREST_FRAME_COLUMNS)]
        return frame.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
