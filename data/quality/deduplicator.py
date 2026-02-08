"""Deduplicate candle/open-interest frames by timestamp."""

from __future__ import annotations

import pandas as pd


class Deduplicator:
    """Remove duplicated rows by timestamp and keep the latest occurrence."""

    @staticmethod
    def deduplicate(data: pd.DataFrame) -> pd.DataFrame:
        if data.empty:
            return data.copy()

        if "timestamp" not in data.columns:
            msg = "DataFrame must contain 'timestamp' column"
            raise ValueError(msg)

        return (
            data.sort_values("timestamp")
            .drop_duplicates(subset=["timestamp"], keep="last")
            .reset_index(drop=True)
        )
