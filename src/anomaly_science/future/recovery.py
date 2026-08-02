"""Censor-aware recovery paths after a causal price snapshot.

The index is strategy-neutral.  A strategy supplies a price that was known at
its snapshot; Core measures only bars whose close/extent becomes available
strictly after that snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from anomaly_science.market_context.sessions import MS_PER_MINUTE


class RecoveryPathError(ValueError):
    """Raised when a recovery path violates the causal minute contract."""


@dataclass(frozen=True, slots=True)
class HorizonRecoveryMetrics:
    horizon_minutes: int
    label_available: bool
    close_return: float | None
    maximum_return: float | None
    minimum_return: float | None


@dataclass(frozen=True, slots=True)
class RecoveryPathMeasurement:
    snapshot_time_ms: int
    future_start_time_ms: int
    available_future_minutes: int
    maximum_horizon_minutes: int
    horizon_complete: bool
    ended_at_data_gap: bool
    gross_break_even_reached: bool
    time_to_gross_break_even_minutes: int | None
    cost_break_even_times_minutes: tuple[int | None, ...]
    horizon_metrics: tuple[HorizonRecoveryMetrics, ...]


class _RangeExtremaIndex:
    def __init__(self, values: np.ndarray, *, mode: str) -> None:
        if mode not in {"minimum", "maximum"}:
            raise RecoveryPathError(f"unknown extrema mode: {mode}")
        self.count = len(values)
        size = 1
        while size < max(1, self.count):
            size *= 2
        self.size = size
        identity = np.inf if mode == "minimum" else -np.inf
        self.tree = np.full(2 * size, identity, dtype=np.float64)
        self.tree[size : size + self.count] = values
        reducer = np.minimum if mode == "minimum" else np.maximum
        for node in range(size - 1, 0, -1):
            self.tree[node] = reducer(self.tree[node * 2], self.tree[node * 2 + 1])
        self.mode = mode

    def query(self, start: int, stop: int) -> float:
        if not 0 <= start < stop <= self.count:
            raise RecoveryPathError("range query is outside the indexed path")
        left = start + self.size
        right = stop + self.size
        result = np.inf if self.mode == "minimum" else -np.inf
        reducer = min if self.mode == "minimum" else max
        while left < right:
            if left & 1:
                result = reducer(result, float(self.tree[left]))
                left += 1
            if right & 1:
                right -= 1
                result = reducer(result, float(self.tree[right]))
            left //= 2
            right //= 2
        return float(result)

    def first_at_or_above(self, start: int, stop: int, threshold: float) -> int | None:
        if self.mode != "maximum":
            raise RecoveryPathError("first_at_or_above requires a maximum index")
        if not 0 <= start <= stop <= self.count:
            raise RecoveryPathError("threshold query is outside the indexed path")
        result = self._first_at_or_above(1, 0, self.size, start, stop, threshold)
        return None if result < 0 or result >= self.count else result

    def _first_at_or_above(
        self,
        node: int,
        left: int,
        right: int,
        query_left: int,
        query_right: int,
        threshold: float,
    ) -> int:
        if right <= query_left or query_right <= left or self.tree[node] < threshold:
            return -1
        if right - left == 1:
            return left
        middle = (left + right) // 2
        first = self._first_at_or_above(
            node * 2,
            left,
            middle,
            query_left,
            query_right,
            threshold,
        )
        if first >= 0:
            return first
        return self._first_at_or_above(
            node * 2 + 1,
            middle,
            right,
            query_left,
            query_right,
            threshold,
        )


class RecoveryPathIndex:
    """O(log n) recovery/extrema queries over one symbol's minute path."""

    def __init__(
        self,
        *,
        timestamps_ms: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
    ) -> None:
        timestamps = np.asarray(timestamps_ms, dtype=np.int64)
        highs = np.asarray(high, dtype=np.float64)
        lows = np.asarray(low, dtype=np.float64)
        closes = np.asarray(close, dtype=np.float64)
        if not (len(timestamps) == len(highs) == len(lows) == len(closes)):
            raise RecoveryPathError("recovery arrays must have equal length")
        if len(timestamps) and bool((np.diff(timestamps) <= 0).any()):
            raise RecoveryPathError("timestamps must be strictly increasing")
        if len(timestamps) and bool((timestamps % MS_PER_MINUTE != 0).any()):
            raise RecoveryPathError("timestamps must be aligned to full minutes")
        for name, values in (("high", highs), ("low", lows), ("close", closes)):
            if bool((~np.isfinite(values)).any()) or bool((values <= 0.0).any()):
                raise RecoveryPathError(f"{name} must contain finite positive prices")
        if bool((highs < lows).any()):
            raise RecoveryPathError("high must be greater than or equal to low")

        self.timestamps_ms = timestamps
        self.high = highs
        self.low = lows
        self.close = closes
        self._high_index = _RangeExtremaIndex(highs, mode="maximum")
        self._low_index = _RangeExtremaIndex(lows, mode="minimum")
        self._contiguous_end = np.empty(len(timestamps), dtype=np.int64)
        if len(timestamps):
            self._contiguous_end[-1] = len(timestamps)
            for index in range(len(timestamps) - 2, -1, -1):
                self._contiguous_end[index] = (
                    self._contiguous_end[index + 1]
                    if timestamps[index + 1] - timestamps[index] == MS_PER_MINUTE
                    else index + 1
                )

    def contiguous_end_exclusive(self, start_index: int) -> int:
        if not 0 <= start_index < len(self.timestamps_ms):
            raise RecoveryPathError("start_index is outside the recovery path")
        return int(self._contiguous_end[start_index])

    def measure(
        self,
        *,
        entry_price: float,
        snapshot_time_ms: int,
        first_future_bar_index: int,
        maximum_horizon_minutes: int,
        horizons_minutes: tuple[int, ...],
        round_trip_cost_bps: tuple[int, ...],
    ) -> RecoveryPathMeasurement:
        if not math.isfinite(entry_price) or entry_price <= 0.0:
            raise RecoveryPathError("entry_price must be finite and positive")
        if maximum_horizon_minutes <= 0:
            raise RecoveryPathError("maximum_horizon_minutes must be positive")
        if tuple(sorted(set(horizons_minutes))) != horizons_minutes:
            raise RecoveryPathError("horizons_minutes must be sorted and unique")
        if not horizons_minutes or horizons_minutes[-1] != maximum_horizon_minutes:
            raise RecoveryPathError("maximum horizon must be the last registered horizon")
        if any(value <= 0 for value in horizons_minutes):
            raise RecoveryPathError("recovery horizons must be positive")
        if tuple(sorted(set(round_trip_cost_bps))) != round_trip_cost_bps:
            raise RecoveryPathError("round-trip cost buffers must be sorted and unique")
        if any(value <= 0 for value in round_trip_cost_bps):
            raise RecoveryPathError("round-trip cost buffers must be positive")
        if not 0 <= first_future_bar_index < len(self.timestamps_ms):
            raise RecoveryPathError("first future bar is outside the indexed path")
        if int(self.timestamps_ms[first_future_bar_index]) != snapshot_time_ms:
            raise RecoveryPathError(
                "the first future bar must open at snapshot_time_ms; its prices become available later"
            )

        contiguous_stop = self.contiguous_end_exclusive(first_future_bar_index)
        requested_stop = min(
            len(self.timestamps_ms),
            first_future_bar_index + maximum_horizon_minutes,
        )
        stop = min(contiguous_stop, requested_stop)
        available = stop - first_future_bar_index
        ended_at_gap = contiguous_stop < requested_stop and contiguous_stop < len(self.timestamps_ms)
        future_start_time_ms = snapshot_time_ms + MS_PER_MINUTE

        gross_index = self._high_index.first_at_or_above(
            first_future_bar_index,
            stop,
            entry_price,
        )
        gross_time = (
            None
            if gross_index is None
            else int(gross_index - first_future_bar_index + 1)
        )
        cost_times: list[int | None] = []
        for cost_bps in round_trip_cost_bps:
            threshold = entry_price * (1.0 + cost_bps / 10_000.0)
            crossing = self._high_index.first_at_or_above(
                first_future_bar_index,
                stop,
                threshold,
            )
            cost_times.append(
                None
                if crossing is None
                else int(crossing - first_future_bar_index + 1)
            )

        horizon_rows: list[HorizonRecoveryMetrics] = []
        for horizon in horizons_minutes:
            label_available = available >= horizon
            if not label_available:
                horizon_rows.append(HorizonRecoveryMetrics(horizon, False, None, None, None))
                continue
            horizon_stop = first_future_bar_index + horizon
            horizon_rows.append(
                HorizonRecoveryMetrics(
                    horizon_minutes=horizon,
                    label_available=True,
                    close_return=float(
                        self.close[horizon_stop - 1] / entry_price - 1.0
                    ),
                    maximum_return=float(
                        self._high_index.query(first_future_bar_index, horizon_stop)
                        / entry_price
                        - 1.0
                    ),
                    minimum_return=float(
                        self._low_index.query(first_future_bar_index, horizon_stop)
                        / entry_price
                        - 1.0
                    ),
                )
            )

        return RecoveryPathMeasurement(
            snapshot_time_ms=snapshot_time_ms,
            future_start_time_ms=future_start_time_ms,
            available_future_minutes=available,
            maximum_horizon_minutes=maximum_horizon_minutes,
            horizon_complete=available >= maximum_horizon_minutes,
            ended_at_data_gap=ended_at_gap,
            gross_break_even_reached=gross_time is not None,
            time_to_gross_break_even_minutes=gross_time,
            cost_break_even_times_minutes=tuple(cost_times),
            horizon_metrics=tuple(horizon_rows),
        )


__all__ = [
    "HorizonRecoveryMetrics",
    "RecoveryPathError",
    "RecoveryPathIndex",
    "RecoveryPathMeasurement",
]
