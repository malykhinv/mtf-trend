"""Generic point-in-time cross-sectional breadth at registered snapshots."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from anomaly_science.market_context.sessions import MS_PER_MINUTE


class MinuteBreadthError(ValueError):
    """Raised when cross-sectional breadth cannot preserve its time contract."""


@dataclass(frozen=True, slots=True)
class MinuteBreadthSpec:
    schema_version: str = "minute_breadth_v1"
    return_lags_minutes: tuple[int, ...] = (5, 15, 60)
    activity_window_minutes: int = 60
    activity_baseline_minutes: int = 1440

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise ValueError("minute breadth schema version is required")
        if tuple(sorted(set(self.return_lags_minutes))) != self.return_lags_minutes:
            raise ValueError("minute breadth return lags must be sorted and unique")
        if any(value <= 0 for value in self.return_lags_minutes):
            raise ValueError("minute breadth return lags must be positive")
        if self.activity_window_minutes <= 0:
            raise ValueError("minute breadth activity window must be positive")
        if self.activity_baseline_minutes < self.activity_window_minutes:
            raise ValueError("minute breadth activity baseline is too short")


def _empty_accumulators(count: int, spec: MinuteBreadthSpec) -> dict[str, np.ndarray]:
    result: dict[str, np.ndarray] = {
        "symbol_count": np.zeros(count, dtype=np.int64),
        "activity_count": np.zeros(count, dtype=np.int64),
        "activity_sum": np.zeros(count, dtype=np.float64),
        "activity_gt_3x": np.zeros(count, dtype=np.int64),
        "activity_gt_10x": np.zeros(count, dtype=np.int64),
    }
    for lag in spec.return_lags_minutes:
        for name, dtype in (
            ("count", np.int64),
            ("sum", np.float64),
            ("sumsq", np.float64),
            ("negative", np.int64),
            ("down_1pct", np.int64),
            ("down_3pct", np.int64),
            ("up_1pct", np.int64),
            ("up_3pct", np.int64),
        ):
            result[f"return_{lag}m_{name}"] = np.zeros(count, dtype=dtype)
    return result


def _update_from_frame(
    accumulator: dict[str, np.ndarray],
    frame: pd.DataFrame,
    snapshot_times_ms: np.ndarray,
    spec: MinuteBreadthSpec,
) -> None:
    required = {"timestamp", "close", "quote_volume"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise MinuteBreadthError(f"minute breadth source missing columns: {missing}")
    if frame.empty:
        return
    timestamp = pd.to_numeric(frame["timestamp"], errors="raise").to_numpy(np.int64)
    close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(float)
    quote = pd.to_numeric(frame["quote_volume"], errors="coerce").to_numpy(float)
    if len(timestamp) and (bool((np.diff(timestamp) <= 0).any()) or bool((close <= 0.0).any())):
        raise MinuteBreadthError("minute breadth timestamps/prices are invalid")
    open_times = snapshot_times_ms - MS_PER_MINUTE
    index = np.searchsorted(timestamp, open_times, side="left")
    exact = (index < len(timestamp))
    exact_indices = np.flatnonzero(exact)
    exact[exact_indices] &= timestamp[index[exact_indices]] == open_times[exact_indices]
    exact &= np.isfinite(np.where(index < len(close), close[np.minimum(index, len(close) - 1)], np.nan))
    accumulator["symbol_count"][exact] += 1
    for lag in spec.return_lags_minutes:
        previous = index - lag
        valid = exact & (previous >= 0)
        positions = np.flatnonzero(valid)
        valid[positions] &= (
            timestamp[index[positions]] - timestamp[previous[positions]]
            == lag * MS_PER_MINUTE
        )
        positions = np.flatnonzero(valid)
        values = np.full(len(snapshot_times_ms), np.nan, dtype=float)
        values[positions] = close[index[positions]] / close[previous[positions]] - 1.0
        finite = valid & np.isfinite(values)
        prefix = f"return_{lag}m_"
        accumulator[prefix + "count"][finite] += 1
        accumulator[prefix + "sum"][finite] += values[finite]
        accumulator[prefix + "sumsq"][finite] += values[finite] ** 2
        accumulator[prefix + "negative"][finite] += values[finite] < 0.0
        accumulator[prefix + "down_1pct"][finite] += values[finite] <= -0.01
        accumulator[prefix + "down_3pct"][finite] += values[finite] <= -0.03
        accumulator[prefix + "up_1pct"][finite] += values[finite] >= 0.01
        accumulator[prefix + "up_3pct"][finite] += values[finite] >= 0.03
    window = spec.activity_window_minutes
    baseline = spec.activity_baseline_minutes
    for output_index in np.flatnonzero(exact):
        end = int(index[output_index])
        activity_start = end - window + 1
        baseline_stop = activity_start
        baseline_start = baseline_stop - baseline
        if baseline_start < 0:
            continue
        if timestamp[end] - timestamp[activity_start] != (window - 1) * MS_PER_MINUTE:
            continue
        if timestamp[baseline_stop - 1] - timestamp[baseline_start] != (baseline - 1) * MS_PER_MINUTE:
            continue
        current = quote[activity_start : end + 1]
        prior = quote[baseline_start:baseline_stop]
        if not bool(np.isfinite(current).all()) or not bool(np.isfinite(prior).all()):
            continue
        denominator = float(np.mean(prior) * window)
        if denominator <= 0.0:
            continue
        ratio = float(np.sum(current) / denominator)
        accumulator["activity_count"][output_index] += 1
        accumulator["activity_sum"][output_index] += ratio
        accumulator["activity_gt_3x"][output_index] += ratio >= 3.0
        accumulator["activity_gt_10x"][output_index] += ratio >= 10.0


def _build_partition(
    paths: tuple[Path, ...],
    snapshot_times_ms: np.ndarray,
    start_time_ms: int,
    end_time_ms_exclusive: int,
    spec: MinuteBreadthSpec,
) -> dict[str, np.ndarray]:
    accumulator = _empty_accumulators(len(snapshot_times_ms), spec)
    history_start = start_time_ms - max(
        spec.activity_baseline_minutes + spec.activity_window_minutes,
        max(spec.return_lags_minutes),
    ) * MS_PER_MINUTE
    for path in paths:
        table = pq.read_table(
            path,
            columns=["timestamp", "close", "quote_volume"],
            filters=[
                ("timestamp", ">=", history_start),
                ("timestamp", "<", end_time_ms_exclusive),
            ],
        )
        _update_from_frame(accumulator, table.to_pandas(), snapshot_times_ms, spec)
    return accumulator


def build_minute_breadth_from_paths(
    paths: Iterable[str | Path],
    *,
    snapshot_times_ms: Iterable[int],
    start_time_ms: int,
    end_time_ms_exclusive: int,
    workers: int = 4,
    spec: MinuteBreadthSpec = MinuteBreadthSpec(),
) -> pd.DataFrame:
    snapshots = np.asarray(sorted(set(int(value) for value in snapshot_times_ms)), dtype=np.int64)
    if not len(snapshots):
        raise MinuteBreadthError("minute breadth requires snapshot times")
    if snapshots[0] < start_time_ms or snapshots[-1] >= end_time_ms_exclusive:
        raise MinuteBreadthError("minute breadth snapshots cross the registered partition")
    source_paths = tuple(sorted((Path(path) for path in paths), key=lambda path: path.stem))
    if not source_paths:
        raise MinuteBreadthError("minute breadth requires source paths")
    if not 1 <= workers <= 8:
        raise MinuteBreadthError("minute breadth workers must be between 1 and 8")
    partitions = tuple(tuple(source_paths[index::workers]) for index in range(workers))
    partitions = tuple(partition for partition in partitions if partition)
    if len(partitions) == 1:
        partials = (
            _build_partition(
                partitions[0], snapshots, start_time_ms, end_time_ms_exclusive, spec
            ),
        )
    else:
        with ProcessPoolExecutor(max_workers=len(partitions)) as executor:
            partials = tuple(
                executor.map(
                    _build_partition,
                    partitions,
                    (snapshots,) * len(partitions),
                    (start_time_ms,) * len(partitions),
                    (end_time_ms_exclusive,) * len(partitions),
                    (spec,) * len(partitions),
                )
            )
    total = _empty_accumulators(len(snapshots), spec)
    for partial in partials:
        for name in total:
            total[name] += partial[name]
    result = pd.DataFrame(
        {
            "snapshot_time_ms": snapshots,
            "breadth_feature_cutoff_time_ms": snapshots,
            "market_breadth_schema_version": spec.schema_version,
            "market_symbol_count": total["symbol_count"],
        }
    )
    for lag in spec.return_lags_minutes:
        prefix = f"return_{lag}m_"
        count = total[prefix + "count"].astype(float)
        mean = np.divide(
            total[prefix + "sum"],
            count,
            out=np.full(len(count), np.nan),
            where=count > 0.0,
        )
        variance = np.divide(
            total[prefix + "sumsq"],
            count,
            out=np.full(len(count), np.nan),
            where=count > 0.0,
        ) - mean * mean
        result[f"market_mean_return_{lag}m"] = mean
        result[f"market_return_dispersion_{lag}m"] = np.sqrt(np.maximum(variance, 0.0))
        for source, target in (
            ("negative", "share_negative"),
            ("down_1pct", "share_down_1pct"),
            ("down_3pct", "share_down_3pct"),
            ("up_1pct", "share_up_1pct"),
            ("up_3pct", "share_up_3pct"),
        ):
            result[f"market_{target}_{lag}m"] = np.divide(
                total[prefix + source],
                count,
                out=np.full(len(count), np.nan),
                where=count > 0.0,
            )
    activity_count = total["activity_count"].astype(float)
    result["market_mean_quote_activity_60m"] = np.divide(
        total["activity_sum"],
        activity_count,
        out=np.full(len(activity_count), np.nan),
        where=activity_count > 0.0,
    )
    result["market_share_quote_activity_gt_3x"] = np.divide(
        total["activity_gt_3x"],
        activity_count,
        out=np.full(len(activity_count), np.nan),
        where=activity_count > 0.0,
    )
    result["market_share_quote_activity_gt_10x"] = np.divide(
        total["activity_gt_10x"],
        activity_count,
        out=np.full(len(activity_count), np.nan),
        where=activity_count > 0.0,
    )
    return result


__all__ = [
    "MinuteBreadthError",
    "MinuteBreadthSpec",
    "build_minute_breadth_from_paths",
]
