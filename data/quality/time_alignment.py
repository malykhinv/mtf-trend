"""Модуль проекта."""

from __future__ import annotations

from logging import getLogger

import pandas as pd

from data.quality.timestamp_normalization import normalize_timestamp_series


logger = getLogger("time-alignment")


class TimeAlignment:
    """Класс."""
    @staticmethod
    def align_to_utc(data: pd.DataFrame) -> pd.DataFrame:
        """Приводит временные метки к единому числовому формату timestamp(ms)."""
        if data.empty:
            return data.copy()

        aligned = data.copy()

        if "timestamp" in aligned.columns:
            timestamp_ms, ts = normalize_timestamp_series(
                timestamp_series=aligned["timestamp"],
                datetime_fallback=aligned.get("datetime"),
                logger=logger,
                log_prefix="time-alignment-normalization",
            )
        elif "datetime" in aligned.columns:
            timestamp_ms, ts = normalize_timestamp_series(
                timestamp_series=aligned["datetime"],
                logger=logger,
                log_prefix="time-alignment-normalization",
            )
        else:
            msg = "DataFrame must contain 'timestamp' or 'datetime' column"
            raise ValueError(msg)

        aligned = aligned.loc[ts.notna()].copy()
        ts = ts.loc[ts.notna()]
        aligned["timestamp"] = timestamp_ms.loc[ts.index].astype("int64")
        aligned["datetime"] = ts

        return aligned.sort_values("timestamp").reset_index(drop=True)
