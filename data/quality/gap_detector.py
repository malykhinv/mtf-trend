"""Gap detection for timeframe-based candle series."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from domain.enums.timeframe import Timeframe

_TIMEFRAME_TO_DELTA = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M30: timedelta(minutes=30),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
    Timeframe.W1: timedelta(weeks=1),
}


class GapDetector:
    """Find missing candle timestamps for a selected timeframe."""

    def detect_gaps(self, data: pd.DataFrame, timeframe: Timeframe) -> list[pd.Timestamp]:
        if data.empty or "timestamp" not in data.columns:
            return []

        ts = pd.to_datetime(data["timestamp"], unit="ms", utc=True, errors="coerce")
        ts = ts.dropna().sort_values().drop_duplicates()
        if ts.empty:
            return []

        expected = pd.date_range(start=ts.iloc[0], end=ts.iloc[-1], freq=_TIMEFRAME_TO_DELTA[timeframe], tz="UTC")
        missing = expected.difference(ts)
        return list(missing)
