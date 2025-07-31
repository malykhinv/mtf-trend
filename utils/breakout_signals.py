from __future__ import annotations

"""Breakout evaluation utilities.

This module provides a function to evaluate breakout conditions using
market data, cluster levels, cumulative volume delta (CVD), open interest
(OI) and volume statistics.  The resulting signals describe potential
trades with entry, stop and take profit levels.
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


def evaluate_breakout(
    ohlcv: pd.DataFrame,
    cluster_levels: pd.DataFrame,
    cvd: pd.Series,
    oi: pd.Series,
    volume_stats: pd.Series,
    funding: float,
    *,
    volume_spike: float = 2.0,
    ema_short: int = 21,
    ema_long: int = 55,
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
    oi:
        Open interest series aligned with ``ohlcv``.
    volume_stats:
        Series containing at least an ``avg_volume`` value.
    funding:
        Current funding rate.  Positive values indicate longs pay shorts.
    volume_spike:
        Multiplier of average volume that must be exceeded to consider a
        breakout valid.
    ema_short, ema_long:
        Spans for the short and long exponential moving averages.
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

    avg_volume = float(volume_stats.get("avg_volume", df["volume"].mean()))
    if avg_volume == 0:
        return []
    vol_ok = last["volume"] > volume_spike * avg_volume

    ema_bull = last["ema_short"] > last["ema_long"]
    ema_bear = last["ema_short"] < last["ema_long"]

    funding_ok_long = funding <= funding_limit
    funding_ok_short = funding >= -funding_limit

    delta_oi = oi.diff().iloc[-1]
    oi_ok_long = delta_oi >= delta_oi_thresh
    oi_ok_short = delta_oi <= -delta_oi_thresh

    cvd_delta = cvd.diff().iloc[-1]
    cvd_ok_long = cvd_delta > 0
    cvd_ok_short = cvd_delta < 0

    signals: List[Signal] = []
    level = cluster_levels.iloc[-1]

    if (
        last["close"] > level["high"]
        and vol_ok
        and ema_bull
        and funding_ok_long
        and oi_ok_long
        and cvd_ok_long
    ):
        entry = float(level["high"])
        stop = float(level["low"])
        risk = entry - stop
        signals.append(Signal("long", entry, stop, entry + risk, entry + 2 * risk))

    if (
        last["close"] < level["low"]
        and vol_ok
        and ema_bear
        and funding_ok_short
        and oi_ok_short
        and cvd_ok_short
    ):
        entry = float(level["low"])
        stop = float(level["high"])
        risk = stop - entry
        signals.append(Signal("short", entry, stop, entry - risk, entry - 2 * risk))

    return signals


__all__ = ["Signal", "evaluate_breakout"]
