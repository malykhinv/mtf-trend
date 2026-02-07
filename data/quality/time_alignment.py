"""Timestamp normalization helpers enforcing UTC parquet contract."""

from __future__ import annotations

import pandas as pd


class TimeAlignment:
    """Normalize all time columns to UTC timestamp-ms + UTC datetime for parquet storage."""

    def align_to_utc(self, data: pd.DataFrame) -> pd.DataFrame:
        if data.empty:
            return data.copy()

        aligned = data.copy()

        if "timestamp" in aligned.columns:
            ts = pd.to_datetime(aligned["timestamp"], unit="ms", utc=True, errors="coerce")
        elif "datetime" in aligned.columns:
            ts = pd.to_datetime(aligned["datetime"], utc=True, errors="coerce")
        else:
            msg = "DataFrame must contain 'timestamp' or 'datetime' column"
            raise ValueError(msg)

        aligned = aligned.loc[ts.notna()].copy()
        ts = ts.loc[ts.notna()]
        aligned["timestamp"] = (ts.astype("int64") // 1_000_000).astype("int64")
        aligned["datetime"] = ts

        return aligned.sort_values("timestamp").reset_index(drop=True)
