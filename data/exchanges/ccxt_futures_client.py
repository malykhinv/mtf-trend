"""CCXT-based futures-only exchange adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from constants import (
    CCXT_MARKET_TYPE_SWAP,
    CCXT_OPTION_DEFAULT_TYPE_KEY,
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

try:
    import ccxt  # type: ignore
except ImportError:  # pragma: no cover
    ccxt = None


_TIMEFRAME_TO_CCXT = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
    Timeframe.W1: "1w",
}


def _to_utc_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return int(value.timestamp() * MILLISECONDS_IN_SECOND)


class CcxtFuturesClient(ExchangeClient):
    """Futures-only implementation for Binance/Bybit/OKX via CCXT."""

    def __init__(
        self,
        exchange: Exchange | str,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        enable_rate_limit: bool = True,
    ) -> None:
        if ccxt is None:
            raise RuntimeError("ccxt is required for CcxtFuturesClient")

        self.exchange = Exchange(str(exchange).upper()) if isinstance(exchange, str) else exchange
        self._client = self._build_client(exchange=self.exchange, api_key=api_key, secret=secret, password=password, enable_rate_limit=enable_rate_limit)
        self._client.load_markets()

    # region Private

    def _build_client(self, exchange: Exchange, api_key: str, secret: str, password: str, enable_rate_limit: bool) -> Any:
        params: dict[str, Any] = {
            "apiKey": api_key,
            "secret": secret,
            "password": password,
            "enableRateLimit": enable_rate_limit,
            "timeout": EXCHANGE_TIMEOUT_SECONDS * MILLISECONDS_IN_SECOND,
        }
        if exchange == Exchange.BINANCE:
            return ccxt.binanceusdm(params)
        if exchange == Exchange.BYBIT:
            params["options"] = {CCXT_OPTION_DEFAULT_TYPE_KEY: CCXT_MARKET_TYPE_SWAP}
            return ccxt.bybit(params)
        if exchange == Exchange.OKX:
            params["options"] = {CCXT_OPTION_DEFAULT_TYPE_KEY: CCXT_MARKET_TYPE_SWAP}
            return ccxt.okx(params)
        raise ValueError(f"Unsupported exchange: {exchange}")

    # endregion Private

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
        tf = _TIMEFRAME_TO_CCXT[timeframe]
        start_ms = _to_utc_ms(start_time)
        since = start_ms
        end_ms = _to_utc_ms(end_time)

        all_rows: list[list[float]] = []
        while since <= end_ms:
            batch = self._client.fetch_ohlcv(symbol, timeframe=tf, since=since, limit=DEFAULT_FETCH_BATCH_SIZE)
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
        if not hasattr(self._client, "fetch_open_interest_history"):
            raise NotImplementedError(f"Exchange {self.exchange.value} does not support fetch_open_interest_history in CCXT")

        tf = _TIMEFRAME_TO_CCXT[timeframe]
        start_ms = _to_utc_ms(start_time)
        since = start_ms
        end_ms = _to_utc_ms(end_time)

        rows: list[dict[str, Any]] = []
        while since <= end_ms:
            try:
                batch = self._client.fetch_open_interest_history(
                    symbol,
                    timeframe=tf,
                    since=since,
                    limit=DEFAULT_FETCH_BATCH_SIZE,
                    params={"intervalTime": tf, "period": tf},
                )
            except TypeError:
                batch = self._client.fetch_open_interest_history(symbol, timeframe=tf, since=since, limit=DEFAULT_FETCH_BATCH_SIZE)
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
