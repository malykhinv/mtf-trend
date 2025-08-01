from __future__ import annotations

"""Breakout evaluation utilities.

This module provides a function to evaluate breakout conditions using
market data, cluster levels, cumulative volume delta (CVD), changes in
open interest (ΔOI) and volume statistics.  The resulting signals
describe potential trades with entry, stop and take profit levels.
"""

from dataclasses import dataclass
from typing import List

import pandas as pd


@dataclass
class Signal:
    """Simple container for trading signals."""

    direction: str
    entry: float
    stop: float
    tp1: float
    tp2: float


def allow_long(ohlcv: pd.DataFrame) -> bool:
    """Return ``True`` if a long trade is allowed.

    A long is permitted when the close is above the 20-period EMA and the
    two most recent candles are bullish.
    """

    if ohlcv.shape[0] < 2:
        return False

    ema20 = ohlcv["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    last = ohlcv.iloc[-1]
    prev = ohlcv.iloc[-2]
    consecutive_up = (last["close"] > last["open"]) and (
        prev["close"] > prev["open"]
    )
    return bool(last["close"] > ema20 and consecutive_up)


def allow_short(ohlcv: pd.DataFrame) -> bool:
    """Return ``True`` if a short trade is allowed.

    A short is permitted when the close is below the 20-period EMA and the
    two most recent candles are bearish.
    """

    if ohlcv.shape[0] < 2:
        return False

    ema20 = ohlcv["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    last = ohlcv.iloc[-1]
    prev = ohlcv.iloc[-2]
    consecutive_down = (last["close"] < last["open"]) and (
        prev["close"] < prev["open"]
    )
    return bool(last["close"] < ema20 and consecutive_down)


def evaluate_breakout(
    ohlcv: pd.DataFrame,
    cluster_levels: pd.DataFrame,
    cvd: pd.Series,
    delta_oi: pd.Series,
    volume_stats: pd.Series,
    funding: float,
    *,
    avg_volume_mult: float = 1.5,
    delta_volume_mult: float = 2.0,
    ema_short: int = 15,
    ema_long: int = 30,
    funding_limit: float = 0.01,
    delta_oi_thresh: float = 0.0,
) -> List[Signal]:
    """Evaluate breakout conditions and return trading signals.

    Parameters
    ----------
    ohlcv:
        OHLCV market data indexed by timestamp.
    cluster_levels:
        DataFrame describing recent range cluster with ``high`` and ``low``
        columns.
    cvd:
        Cumulative volume delta series aligned with ``ohlcv``.
    delta_oi:
        Change in open interest aligned with ``ohlcv``. Positive values
        indicate increasing participation.
    volume_stats:
        Deprecated.  Retained for backward compatibility but ignored.
    funding:
        Current funding rate.  Positive values indicate longs pay shorts.
    avg_volume_mult:
        Multiplier applied to the rolling average of volume.  The latest
        volume must exceed ``avg_volume_mult`` × the rolling average.
    delta_volume_mult:
        Multiplier applied to the rolling standard deviation of volume
        changes.  The change in volume must exceed ``delta_volume_mult`` ×
        the rolling standard deviation.
    ema_short, ema_long:
        Spans for the short and long exponential moving averages (typically
        5–15 and 10–30 respectively).
    funding_limit:
        Maximum absolute funding rate allowed for signals.
    delta_oi_thresh:
        Minimum absolute change in open interest required.

    Returns
    -------
    List[Signal]
        Potential breakout signals.  The list will be empty if no valid
        setups are found.
    """

    df = ohlcv.copy()
    if df.empty or cluster_levels.empty:
        return []

    df["ema_short"] = df["close"].ewm(span=ema_short, adjust=False).mean()
    df["ema_long"] = df["close"].ewm(span=ema_long, adjust=False).mean()
    last = df.iloc[-1]

    vol_window = df["volume"].rolling(window=15, min_periods=1)
    avg_volume = vol_window.mean().iloc[-1]
    vol_sigma = vol_window.std().iloc[-1]
    vol_delta = df["volume"].diff().iloc[-1]
    if pd.isna(avg_volume) or pd.isna(vol_sigma) or pd.isna(vol_delta):
        return []
    vol_ok = (
        last["volume"] > avg_volume_mult * avg_volume
        and vol_delta > delta_volume_mult * vol_sigma
    )

    ema_bull = last["ema_short"] > last["ema_long"]
    ema_bear = last["ema_short"] < last["ema_long"]

    funding_ok_long = funding <= funding_limit
    funding_ok_short = funding >= -funding_limit

    delta_oi_last = delta_oi.iloc[-1] if not delta_oi.empty else 0.0
    oi_ok = delta_oi_last > delta_oi_thresh

    cvd_smoothed = cvd.ewm(span=3, adjust=False).mean()
    cvd_delta = cvd_smoothed.diff().iloc[-1]
    cvd_ok_long = cvd_delta > 0
    cvd_ok_short = cvd_delta < 0

    signals: List[Signal] = []
    level = cluster_levels.iloc[-1]

    if (
        last["close"] > level["high"]
        and vol_ok
        and ema_bull
        and funding_ok_long
        and oi_ok
        and cvd_ok_long
        and allow_long(df)
    ):
        entry = float(level["high"])
        cluster_stop = float(level["low"])
        risk = max(entry - cluster_stop, 0.004 * entry)
        stop = entry - risk
        signals.append(
            Signal(
                "long",
                entry,
                stop,
                entry + 1.5 * risk,
                entry + 3 * risk,
            )
        )

    if (
        last["close"] < level["low"]
        and vol_ok
        and ema_bear
        and funding_ok_short
        and oi_ok
        and cvd_ok_short
        and allow_short(df)
    ):
        entry = float(level["low"])
        cluster_stop = float(level["high"])
        risk = max(cluster_stop - entry, 0.004 * entry)
        stop = entry + risk
        signals.append(
            Signal(
                "short",
                entry,
                stop,
                entry - 1.5 * risk,
                entry - 3 * risk,
            )
        )

    return signals


__all__ = ["Signal", "evaluate_breakout", "allow_long", "allow_short"]
