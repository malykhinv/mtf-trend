"""Generic OHLCV loading and resampling for annotation tools."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow.dataset as ds

LOAD_COLS = ("timestamp", "open", "high", "low", "close", "quote_volume", "trade_count")


def load_ohlcv_parquet(path: Path) -> dict[str, np.ndarray]:
    table = ds.dataset(path).to_table(columns=list(LOAD_COLS))
    cols = {c: table.column(c).to_numpy(zero_copy_only=False) for c in LOAD_COLS}
    cols["timestamp"] = cols["timestamp"].astype(np.int64)
    for col in LOAD_COLS[1:]:
        cols[col] = cols[col].astype(np.float64)
    return cols


def resample_ohlcv_np(cols: dict[str, np.ndarray], tf_min: int) -> dict[str, np.ndarray]:
    ts = cols["timestamp"]
    n = len(ts)
    if n == 0 or tf_min == 1:
        return {c: cols[c] for c in LOAD_COLS}
    bucket = ts // (tf_min * 60_000)
    starts = np.concatenate(([0], np.nonzero(np.diff(bucket))[0] + 1))
    ends = np.append(starts[1:], n) - 1
    return {
        "timestamp": bucket[starts] * (tf_min * 60_000),
        "open": cols["open"][starts],
        "high": np.maximum.reduceat(cols["high"], starts),
        "low": np.minimum.reduceat(cols["low"], starts),
        "close": cols["close"][ends],
        "quote_volume": np.add.reduceat(cols["quote_volume"], starts),
        "trade_count": np.add.reduceat(cols["trade_count"], starts),
    }

