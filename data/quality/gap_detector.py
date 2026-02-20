"""Модуль проекта."""

from __future__ import annotations

from numbers import Integral

import pandas as pd


class GapDetector:
    """Класс."""

    _UNIX_MS_MIN = 1_000_000_000_000
    _UNIX_MS_MAX = 9_999_999_999_999

    @classmethod
    def _is_unix_ms(cls, value: object) -> bool:
        return (
            isinstance(value, Integral)
            and not isinstance(value, bool)
            and cls._UNIX_MS_MIN <= int(value) <= cls._UNIX_MS_MAX
        )

    @staticmethod
    def _infer_step_ms(sorted_timestamps: list[int]) -> int | None:
        diffs = [curr - prev for prev, curr in zip(sorted_timestamps, sorted_timestamps[1:]) if curr > prev]
        if not diffs:
            return None
        return min(diffs)

    @staticmethod
    def detect_gaps(data: pd.DataFrame, expected_step_ms: int | None = None) -> list[int]:
        """Ищет пропуски во временном ряду свечей по исходным меткам времени биржи."""
        if data.empty or "timestamp" not in data.columns:
            return []

        timestamps = data["timestamp"].dropna()
        if timestamps.empty:
            return []

        if not timestamps.map(GapDetector._is_unix_ms).all():
            return []

        ts = sorted(set(timestamps.tolist()))
        step = expected_step_ms if expected_step_ms is not None else GapDetector._infer_step_ms(ts)
        if step is None or step <= 0:
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
