"""Utilities for downloading OHLCV data using ccxt.

This module provides an ``update_data`` function that downloads OHLCV
candles for a given symbol and appends them to ``data/raw_data``.  The
function is resilient to temporary network disconnects and rate limits
by retrying failed requests with exponential backoff.

``fetch_all_from_config`` is a small helper that reads the symbols from a
configuration dictionary and updates data for each of them.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Iterable, List

import ccxt
import pandas as pd
import yaml


def _safe_fetch(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since: int | None,
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
    limit: int = 1000,
    data_dir: str | Path = "data/raw_data",
    exchange_name: str = "binance",
    max_retries: int = 5,
) -> Path:
    """Fetch OHLCV candles and store them as a CSV file.

    The resulting CSV will be located at ``data_dir/{symbol}_{timeframe}.csv``
    where the ``/`` in the symbol is removed.  Existing files are appended to
    and duplicates are removed.
    """

    exchange_class = getattr(ccxt, exchange_name)
    exchange = exchange_class({"enableRateLimit": True})

    since = int(pd.Timestamp(start).timestamp() * 1000)
    end_ts = int(pd.Timestamp(end).timestamp() * 1000)

    all_ohlcv: List[list] = []

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
    file_path = data_path / f"{symbol.replace('/', '')}_{timeframe}.csv"

    if file_path.exists() and not df.empty:
        existing = pd.read_csv(file_path, parse_dates=["timestamp"])
        df = (
            pd.concat([existing, df])
            .drop_duplicates(subset="timestamp")
            .sort_values("timestamp")
        )

    df.to_csv(file_path, index=False)
    return file_path


def _timeframe_to_seconds(timeframe: str) -> int:
    """Convert a ccxt timeframe string (e.g. ``1m``) to seconds."""

    unit = timeframe[-1]
    amount = int(timeframe[:-1])
    if unit == "m":
        return amount * 60
    if unit == "h":
        return amount * 60 * 60
    if unit == "d":
        return amount * 60 * 60 * 24
    if unit == "w":
        return amount * 60 * 60 * 24 * 7
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def fetch_timeframes(
    symbol: str,
    timeframes: Iterable[str],
    *,
    limit: int = 1000,
    data_dir: str | Path = "data/raw_data",
    exchange_name: str = "binance",
    max_retries: int = 5,
) -> List[Path]:
    """Fetch recent OHLCV data for multiple timeframes.

    Parameters are passed through to :func:`update_data` with ``start`` and
    ``end`` computed from the ``limit`` and each timeframe.
    """

    end = pd.Timestamp.utcnow()
    results: List[Path] = []
    for tf in timeframes:
        span = _timeframe_to_seconds(tf) * limit
        start = end - pd.Timedelta(seconds=span)
        results.append(
            update_data(
                symbol,
                start,
                end,
                timeframe=tf,
                limit=limit,
                data_dir=data_dir,
                exchange_name=exchange_name,
                max_retries=max_retries,
            )
        )

    return results


def fetch_all_from_config(
    config: dict,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    *,
    timeframe: str = "1m",
) -> List[Path]:
    """Fetch data for all symbols defined in the configuration dict.

    Parameters
    ----------
    config:
        Configuration dictionary containing at least a ``symbols`` list and
        optional ``data_paths`` entry with ``data_dir``.
    start, end:
        Time range for which to fetch data.
    timeframe:
        Candle timeframe passed through to :func:`update_data`.
    """

    data_dir = Path(config.get("data_paths", {}).get("data_dir", "data")) / "raw_data"
    symbols: Iterable[str] = config.get("symbols", [])
    results: List[Path] = []

    for symbol in symbols:
        results.append(
            update_data(symbol, start, end, timeframe=timeframe, data_dir=data_dir)
        )

    return results


def _load_config(path: str | Path) -> dict:
    if Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch OHLCV data for a symbol")
    parser.add_argument("--symbol", help="Trading pair symbol, e.g. BTC/USDT")
    parser.add_argument(
        "--limit", type=int, help="Number of candles to fetch per timeframe"
    )
    parser.add_argument(
        "--config", default="config.yaml", help="Path to configuration file"
    )
    args = parser.parse_args()

    config = _load_config(args.config)
    fetcher_cfg = config.get("fetcher", {})

    symbol = args.symbol or fetcher_cfg.get("symbol")
    if symbol is None:
        raise ValueError("symbol must be provided via --symbol or config.yaml")

    limit = args.limit or fetcher_cfg.get("limit", 1000)
    timeframes: List[str] = fetcher_cfg.get("timeframes", ["1m"])

    data_dir = Path(config.get("data_paths", {}).get("data_dir", "data")) / "raw_data"

    fetch_timeframes(symbol, timeframes, limit=limit, data_dir=data_dir)


__all__ = ["update_data", "fetch_all_from_config", "fetch_timeframes"]


if __name__ == "__main__":
    main()

