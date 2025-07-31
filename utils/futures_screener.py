"""Futures market screener.

Fetches various metrics (24h/1h volume, spread, ATR, ATR growth, and
standard deviation growth) for all futures symbols on an exchange and
selects top candidates according to basic volume rule and a maximum
number of positions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import ccxt
import numpy as np
import pandas as pd


@dataclass
class SymbolMetrics:
    symbol: str
    vol_24h: float
    vol_1h: float
    spread: float
    midpoint: float
    atr: float
    atr_growth: float
    stddev_growth: float


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr


def atr_from_ohlcv(df: pd.DataFrame, period: int = 14) -> float:
    tr = true_range(df["high"], df["low"], df["close"])
    atr = tr.rolling(window=period).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else 0.0


def stddev_growth(ohlcv_1m: pd.DataFrame, ohlcv_1h: pd.DataFrame) -> float:
    """Return ratio of 1h std dev to 24h std dev of log returns."""
    ret_1m = np.log(ohlcv_1m["close"]).diff().dropna()
    std_1h = ret_1m.std()

    ret_1h = np.log(ohlcv_1h["close"]).diff().dropna()
    std_24h = ret_1h.std()

    if std_24h == 0 or np.isnan(std_24h):
        return 0.0
    return float(std_1h / std_24h)


def fetch_metrics(exchange: ccxt.Exchange, symbol: str) -> SymbolMetrics | None:
    try:
        ticker = exchange.fetch_ticker(symbol)
        vol_24h = ticker.get("quoteVolume") or 0.0
        bid = ticker.get("bid") or 0.0
        ask = ticker.get("ask") or 0.0
        spread = ask - bid
        midpoint = (ask + bid) / 2 if ask and bid else 0.0

        ohlcv_1h = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=25)
        df_1h = pd.DataFrame(
            ohlcv_1h, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        vol_1h = float(df_1h.iloc[-1]["volume"]) if not df_1h.empty else 0.0
        atr = atr_from_ohlcv(df_1h)

        ohlcv_1m = exchange.fetch_ohlcv(symbol, timeframe="1m", limit=60)
        df_1m = pd.DataFrame(
            ohlcv_1m, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        sd_growth = stddev_growth(df_1m, df_1h)

        atr_recent = atr_from_ohlcv(df_1m.iloc[-30:])
        atr_prev = atr_from_ohlcv(df_1m.iloc[:30])
        atr_growth = (atr_recent / atr_prev) if atr_prev else 0.0

        return SymbolMetrics(
            symbol, vol_24h, vol_1h, spread, midpoint, atr, atr_growth, sd_growth
        )
    except Exception:
        return None


def screen_futures(
    exchange_name: str = "binanceusdm",
    min_24h_volume: float = 3_000_000.0,
    max_positions: int = 10,
) -> List[SymbolMetrics]:
    exchange_class = getattr(ccxt, exchange_name)
    exchange = exchange_class({"enableRateLimit": True})
    markets = exchange.load_markets()

    metrics: List[SymbolMetrics] = []

    # fetch all tickers once to avoid excessive API calls
    tickers = exchange.fetch_tickers()

    for market in markets.values():
        if not market.get("contract"):
            continue
        symbol = market["symbol"]
        ticker = tickers.get(symbol)
        if not ticker:
            continue
        vol_24h = ticker.get("quoteVolume") or 0.0
        if vol_24h < min_24h_volume:
            continue

        m = fetch_metrics(exchange, symbol)
        if not m:
            continue
        # Additional screening conditions
        if m.vol_1h < 200_000:
            continue
        if not m.midpoint or m.spread / m.midpoint > 0.0025:
            continue
        if m.atr_growth < 1.25:
            continue
        if m.stddev_growth < 1.20:
            continue
        metrics.append(m)

    metrics.sort(key=lambda m: m.atr, reverse=True)
    return metrics[:max_positions]


if __name__ == "__main__":
    selected = screen_futures()
    for m in selected:
        print(
            f"{m.symbol}: vol24h={m.vol_24h:,.0f} vol1h={m.vol_1h:,.0f} "
            f"spread={m.spread:.6f} atr={m.atr:.6f} "
            f"atr_growth={m.atr_growth:.2f} sd_growth={m.stddev_growth:.3f}"
        )
