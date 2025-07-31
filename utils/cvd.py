"""Utilities for computing cumulative volume delta (CVD).

This module contains helpers to derive bid/ask volume from raw trade data and
calculate the cumulative volume delta.  The data returned is indexed by
``pandas.Timestamp`` allowing easy alignment with other time series.

The public API exposes ``compute_bid_ask_volume``, ``compute_cvd`` and
``get_cvd``.
"""

from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

try:  # ``ccxt`` is only needed when using ``get_cvd``
    import ccxt  # type: ignore
except Exception:  # pragma: no cover - ccxt may be missing in tests
    ccxt = None  # type: ignore


def _find_volume_column(df: pd.DataFrame) -> str:
    """Return the column that represents trade size.

    The helper searches for a set of common column names used by different
    exchanges.  A ``ValueError`` is raised when no suitable column can be
    identified.
    """

    candidates: Iterable[str] = ("amount", "volume", "qty", "quantity", "size")
    for col in candidates:
        if col in df.columns:
            return col
    raise ValueError("Trades DataFrame must contain a volume column")


def compute_bid_ask_volume(trades: pd.DataFrame, timeframe: str = "1m") -> pd.DataFrame:
    """Aggregate trades into bid and ask volume per timeframe.

    Parameters
    ----------
    trades:
        DataFrame containing either raw trade data (with ``side`` or
        ``is_buyer_maker`` columns) or pre-aggregated volume columns
        ``bid_volume`` and ``ask_volume``.
    timeframe:
        Pandas offset alias used to resample the data (``'1m'`` by default).

    Returns
    -------
    DataFrame
        A DataFrame indexed by timestamp containing ``bid_volume`` and
        ``ask_volume`` columns.
    """

    if trades.empty:
        return pd.DataFrame(columns=["bid_volume", "ask_volume"]).astype(float)

    df = trades.copy()
    if "timestamp" not in df.columns:
        raise ValueError("Trades DataFrame must contain a 'timestamp' column")

    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # Already aggregated data
    if {"bid_volume", "ask_volume"}.issubset(df.columns):
        return (
            df.set_index("timestamp")[["bid_volume", "ask_volume"]]
            .resample(timeframe)
            .sum()
        )

    volume_col = _find_volume_column(df)

    if "is_buyer_maker" in df.columns:
        # True means the buyer provided liquidity -> trade executed at bid
        bid_mask = df["is_buyer_maker"].astype(bool)
    elif "side" in df.columns:
        # 'sell' trades occur at the bid
        bid_mask = df["side"].str.lower().isin(["sell", "s"])
    else:
        raise ValueError(
            "Trades must contain either 'side' or 'is_buyer_maker' column to determine direction"
        )

    df["bid_volume"] = 0.0
    df["ask_volume"] = 0.0
    df.loc[bid_mask, "bid_volume"] = df.loc[bid_mask, volume_col].astype(float)
    df.loc[~bid_mask, "ask_volume"] = df.loc[~bid_mask, volume_col].astype(float)

    return (
        df.set_index("timestamp")[["bid_volume", "ask_volume"]]
        .resample(timeframe)
        .sum()
    )


def compute_cvd(
    trades: pd.DataFrame,
    timeframe: str = "1m",
    ema_span: Optional[int] = None,
) -> pd.Series:
    """Calculate the cumulative volume delta from trade data.

    Parameters
    ----------
    trades:
        DataFrame of trades or aggregated volume.
    timeframe:
        Resampling frequency for aggregation.
    ema_span:
        If provided, an exponential moving average with ``span`` is applied to
        the resulting CVD series.

    Returns
    -------
    pandas.Series
        Series indexed by timestamp representing the cumulative volume delta.
    """

    vols = compute_bid_ask_volume(trades, timeframe)
    delta = vols["ask_volume"] - vols["bid_volume"]
    cvd = delta.cumsum()

    if ema_span is not None:
        cvd = cvd.ewm(span=ema_span, adjust=False).mean()

    cvd.name = "cvd"
    return cvd


def get_cvd(
    symbol: str,
    timeframe: str,
    *,
    exchange_name: str = "binance",
    limit: int = 1000,
    ema_span: Optional[int] = None,
) -> pd.Series:
    """Fetch recent trades via ``ccxt`` and return the CVD series.

    Parameters
    ----------
    symbol:
        Market symbol, e.g. ``"BTC/USDT"``.
    timeframe:
        Resampling frequency.
    exchange_name:
        Name of the exchange supported by ccxt.  ``"binance"`` by default.
    limit:
        Number of recent trades to fetch.  Exchanges usually cap this to 1000.
    ema_span:
        Optional span for EMA smoothing of the CVD.

    Returns
    -------
    pandas.Series
        The cumulative volume delta indexed by timestamp.  An empty series is
        returned if no trades are available or if ``ccxt`` is not installed.
    """

    if ccxt is None:
        return pd.Series(dtype="float64")

    exchange_class = getattr(ccxt, exchange_name)
    exchange = exchange_class({"enableRateLimit": True})
    since = exchange.milliseconds() - exchange.parse_timeframe(timeframe) * limit * 1000

    trades = exchange.fetch_trades(symbol, since=since, limit=limit)
    if not trades:
        return pd.Series(dtype="float64")

    df = pd.DataFrame(trades)
    # Keep only the necessary columns
    keep_cols = [c for c in ["timestamp", "side", "amount", "is_buyer_maker"] if c in df.columns]
    df = df[keep_cols]
    return compute_cvd(df, timeframe=timeframe, ema_span=ema_span)


__all__ = ["compute_bid_ask_volume", "compute_cvd", "get_cvd"]
