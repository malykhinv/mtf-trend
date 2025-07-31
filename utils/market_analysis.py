from __future__ import annotations

"""Helpers for analysing market conditions using 5 minute candles.

The module provides small utility functions to download recent candles for
BTC and ETH and to evaluate simple trend conditions such as consecutive
moves and the relation of price to a moving average.
"""

from typing import Dict, Iterable

import ccxt
import pandas as pd

SYMBOLS: Iterable[str] = ("BTC/USDT", "ETH/USDT")


def load_5m_candles(symbol: str, limit: int = 100, exchange: ccxt.Exchange | None = None) -> pd.DataFrame:
    """Return recent 5 minute candles for ``symbol`` as a ``DataFrame``.

    Parameters
    ----------
    symbol:
        Market pair such as ``"BTC/USDT"``.
    limit:
        Number of candles to fetch.  Defaults to 100.
    exchange:
        Optional pre-configured ccxt exchange instance.  When ``None`` a
        regular binance spot instance is created.
    """

    exchange = exchange or ccxt.binance()
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe="5m", limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def load_btc_eth_candles(limit: int = 100, exchange: ccxt.Exchange | None = None) -> Dict[str, pd.DataFrame]:
    """Convenience wrapper returning candles for both BTC and ETH."""

    return {symbol: load_5m_candles(symbol, limit, exchange) for symbol in SYMBOLS}


def has_consecutive_move(df: pd.DataFrame, direction: str, minutes: int = 5) -> bool:
    """Return ``True`` if the close moved ``direction`` for ``minutes`` candles.

    ``direction`` may be ``"up"`` or ``"down"``.  The function looks at the
    last ``minutes`` *completed* candles and ensures each close is greater or
    smaller than the previous one accordingly.
    """

    if df is None or df.empty or len(df) < minutes + 1:
        return False
    closes = df["close"].tail(minutes + 1)
    diffs = closes.diff().dropna()
    if direction.lower() == "up":
        return (diffs > 0).all()
    if direction.lower() == "down":
        return (diffs < 0).all()
    raise ValueError("direction must be 'up' or 'down'")


def price_above_ema(df: pd.DataFrame, period: int = 20) -> bool:
    """Return ``True`` if the latest close is above its EMA."""

    if df is None or df.empty:
        return False
    ema = df["close"].ewm(span=period, adjust=False).mean()
    return df["close"].iloc[-1] > ema.iloc[-1]
