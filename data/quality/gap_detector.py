"""Модуль проекта."""

from __future__ import annotations

import pandas as pd

from constants import MILLISECONDS_IN_SECOND, TIMEFRAME_TO_DELTA
from domain.enums.timeframe import Timeframe

_TIMEFRAME_TO_MS = {
    timeframe: int(delta.total_seconds() * MILLISECONDS_IN_SECOND)
    for timeframe, delta in TIMEFRAME_TO_DELTA.items()
}


class GapDetector:
    """Класс."""
    @staticmethod
    def detect_gaps(data: pd.DataFrame, timeframe: Timeframe) -> list[int]:
        """Ищет пропуски во временном ряду свечей по исходным меткам времени биржи."""
        if data.empty or "timestamp" not in data.columns:
            return []

        timestamps = pd.to_numeric(data["timestamp"], errors="coerce").dropna().astype("int64")
        if timestamps.empty:
            return []

        ts = sorted(set(timestamps.tolist()))
        step = _TIMEFRAME_TO_MS[timeframe]
        if step <= 0:
            return []

        missing: list[int] = []
        for prev, curr in zip(ts, ts[1:]):
            gap = curr - prev
            if gap <= step:
                continue
            next_expected = prev + step
            while next_expected < curr:
                missing.append(next_expected)
                next_expected += step

        return missing
