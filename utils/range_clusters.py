from __future__ import annotations

"""Detect tight trading ranges based on ATR and visualize them."""

from dataclasses import dataclass
from typing import List

import pandas as pd


@dataclass
class RangeCluster:
    """Simple container describing a tight range cluster."""

    start: pd.Timestamp
    end: pd.Timestamp
    high: float
    low: float
    duration: int


def find_tight_range_clusters(
    ohlc: pd.DataFrame,
    atr_period: int = 14,
    atr_multiplier: float = 0.5,
    min_bars: int = 10,
    max_bars: int = 30,
) -> pd.DataFrame:
    """Return clusters of consecutive small-range candles.

    Parameters
    ----------
    ohlc:
        DataFrame containing ``high``, ``low`` and ``close`` columns.  A
        ``timestamp`` column is optional; if present it will be used as the
        index.
    atr_period:
        Number of bars used for the ATR calculation.  ``14`` by default.
    atr_multiplier:
        Maximum fraction of the ATR that each candle may span.  ``0.5`` means
        the candle's high--low range must not exceed half the ATR.
    min_bars, max_bars:
        Minimum and maximum length (in bars) of a cluster to be returned.

    Returns
    -------
    pandas.DataFrame
        DataFrame describing each cluster with ``start``, ``end``, ``high``,
        ``low`` and ``duration`` columns.
    """

    df = ohlc.copy()
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")

    required = {"high", "low", "close"}
    if not required.issubset(df.columns):
        missing = ", ".join(sorted(required - set(df.columns)))
        raise ValueError(f"DataFrame must contain columns: {missing}")

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()

    small = high_low <= atr_multiplier * atr

    clusters: List[RangeCluster] = []
    start_idx: int | None = None

    for i, is_small in enumerate(small):
        if is_small and start_idx is None:
            start_idx = i
        elif not is_small and start_idx is not None:
            end_idx = i
            duration = end_idx - start_idx
            if min_bars <= duration <= max_bars:
                sub = df.iloc[start_idx:end_idx]
                clusters.append(
                    RangeCluster(
                        start=sub.index[0],
                        end=sub.index[-1],
                        high=float(sub["high"].max()),
                        low=float(sub["low"].min()),
                        duration=duration,
                    )
                )
            start_idx = None

    if start_idx is not None:
        duration = len(df) - start_idx
        if min_bars <= duration <= max_bars:
            sub = df.iloc[start_idx:]
            clusters.append(
                RangeCluster(
                    start=sub.index[0],
                    end=sub.index[-1],
                    high=float(sub["high"].max()),
                    low=float(sub["low"].min()),
                    duration=duration,
                )
            )

    return pd.DataFrame(clusters)


def plot_clusters(ohlc: pd.DataFrame, clusters: pd.DataFrame) -> None:
    """Visualize clusters by highlighting them on a price chart.

    Parameters
    ----------
    ohlc:
        DataFrame with a ``close`` column and an index representing time (or a
        ``timestamp`` column which will be used as index).
    clusters:
        DataFrame produced by :func:`find_tight_range_clusters`.
    """

    df = ohlc.copy()
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")

    try:
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - only executed without mpl
        raise RuntimeError("matplotlib is required for plotting") from exc

    ax = df["close"].plot(figsize=(10, 4))
    for _, row in clusters.iterrows():
        ax.axvspan(row["start"], row["end"], color="orange", alpha=0.3)
    ax.set_title("Tight Range Clusters")
    ax.set_ylabel("Price")
    plt.show()


__all__ = ["find_tight_range_clusters", "plot_clusters"]
