"""Utilities for downloading OHLCV data using ccxt.

This module provides an ``update_data`` function that downloads OHLCV
candles for a given symbol and appends them to ``data/raw_data``.  The
function is resilient to temporary network disconnects and rate limits
by retrying failed requests with exponential backoff.

``fetch_all_from_config`` is a small helper that reads the symbols from a
configuration dictionary and updates data for each of them.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, List

import ccxt
import pandas as pd


def _safe_fetch(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since: int,
    limit: int,
    max_retries: int,
) -> List[list]:
    """Fetch OHLCV data with retries.

    ccxt may raise ``NetworkError`` or ``RateLimitExceeded``.  In these
    cases the function sleeps for an increasing amount of time and
    retries the request.  After ``max_retries`` attempts a ``RuntimeError``
    is raised.
    """

    for attempt in range(max_retries):
        try:
            return exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, since=since, limit=limit
            )
        except (ccxt.NetworkError, ccxt.RateLimitExceeded):
            wait = (attempt + 1) * (exchange.rateLimit / 1000)
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {symbol} after {max_retries} retries")


def update_data(
    symbol: str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    timeframe: str = "1m",
    data_dir: str | Path = "data/raw_data",
    exchange_name: str = "binance",
    max_retries: int = 5,
) -> Path:
    """Fetch OHLCV candles and store them as a CSV file.

    The resulting CSV will be located at ``data_dir/{symbol}.csv`` where
    the ``/`` in the symbol is removed.  Existing files are appended to
    and duplicates are removed.
    """

    exchange_class = getattr(ccxt, exchange_name)
    exchange = exchange_class({"enableRateLimit": True})

    since = int(pd.Timestamp(start).timestamp() * 1000)
    end_ts = int(pd.Timestamp(end).timestamp() * 1000)

    all_ohlcv: List[list] = []
    limit = 1000

    while since < end_ts:
        ohlcv = _safe_fetch(exchange, symbol, timeframe, since, limit, max_retries)
        if not ohlcv:
            break

        last = ohlcv[-1][0]
        since = last + 1
        all_ohlcv.extend(ohlcv)

        if last >= end_ts:
            break

        time.sleep(exchange.rateLimit / 1000)

    df = pd.DataFrame(
        all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )

    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df[df["timestamp"] <= pd.Timestamp(end)]

    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    file_path = data_path / f"{symbol.replace('/', '')}.csv"

    if file_path.exists() and not df.empty:
        existing = pd.read_csv(file_path, parse_dates=["timestamp"])
        df = (
            pd.concat([existing, df])
            .drop_duplicates(subset="timestamp")
            .sort_values("timestamp")
        )

    df.to_csv(file_path, index=False)
    return file_path


def fetch_all_from_config(
    config: dict, start: str | pd.Timestamp, end: str | pd.Timestamp
) -> List[Path]:
    """Fetch data for all symbols defined in the configuration dict."""

    data_dir = Path(config.get("data_paths", {}).get("data_dir", "data")) / "raw_data"
    symbols: Iterable[str] = config.get("symbols", [])
    results: List[Path] = []

    for symbol in symbols:
        results.append(update_data(symbol, start, end, data_dir=data_dir))

    return results


__all__ = ["update_data", "fetch_all_from_config"]

