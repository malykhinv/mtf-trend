"""Physical IS-only input boundary for the residual-absorption research."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow.parquet as pq

from anomaly_science.strategy.residual_absorption.spec import (
    RESIDUAL_ABSORPTION_RESEARCH_SPLIT,
)

ONE_MINUTE_MS = 60_000


class ResidualAbsorptionDataError(ValueError):
    """Raised when a strategy input could cross the registered IS boundary."""


def is_last_open_time_ms_exclusive(*, interval_ms: int = ONE_MINUTE_MS) -> int:
    if interval_ms <= 0:
        raise ResidualAbsorptionDataError("interval_ms must be positive")
    return RESIDUAL_ABSORPTION_RESEARCH_SPLIT.oos_start_time_ms - interval_ms


def read_is_parquet_schema(path: str | Path) -> tuple[str, ...]:
    """Read schema metadata without materializing any IS or OOS market row."""

    return tuple(pq.read_schema(Path(path)).names)


def read_is_symbol_minutes(
    path: str | Path,
    *,
    columns: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Read only closed one-minute rows whose availability remains inside IS."""

    source = Path(path)
    requested = None if columns is None else tuple(dict.fromkeys(("timestamp", *columns)))
    table = pq.read_table(
        source,
        columns=None if requested is None else list(requested),
        filters=[
            ("timestamp", ">=", RESIDUAL_ABSORPTION_RESEARCH_SPLIT.is_start_time_ms),
            ("timestamp", "<", is_last_open_time_ms_exclusive()),
        ],
    )
    frame = table.to_pandas()
    if "timestamp" not in frame.columns:
        raise ResidualAbsorptionDataError("IS minute input must contain timestamp")
    timestamp = pd.to_numeric(frame["timestamp"], errors="raise")
    if bool((timestamp < RESIDUAL_ABSORPTION_RESEARCH_SPLIT.is_start_time_ms).any()):
        raise ResidualAbsorptionDataError("reader returned a row before registered IS")
    if bool((timestamp >= is_last_open_time_ms_exclusive()).any()):
        raise ResidualAbsorptionDataError("reader returned a row whose close reaches OOS")
    return frame.sort_values("timestamp", kind="mergesort").reset_index(drop=True)


def read_is_event_window_minutes(
    path: str | Path,
    *,
    snapshot_times_ms: Iterable[int],
    window_minutes: int,
    columns: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Physically read the union of closed IS minutes needed by event windows."""

    if window_minutes <= 0:
        raise ResidualAbsorptionDataError("window_minutes must be positive")
    snapshots = tuple(sorted({int(value) for value in snapshot_times_ms}))
    if not snapshots:
        return pd.DataFrame(columns=("timestamp", *(columns or ())))
    for snapshot in snapshots:
        RESIDUAL_ABSORPTION_RESEARCH_SPLIT.require_is_timestamp_ms(snapshot)
    offsets = range(window_minutes, 0, -1)
    required_times = sorted(
        {
            snapshot - offset * ONE_MINUTE_MS
            for snapshot in snapshots
            for offset in offsets
        }
    )
    requested = None if columns is None else tuple(dict.fromkeys(("timestamp", *columns)))
    table = pq.read_table(
        Path(path),
        columns=None if requested is None else list(requested),
        filters=[
            ("timestamp", ">=", RESIDUAL_ABSORPTION_RESEARCH_SPLIT.is_start_time_ms),
            ("timestamp", "<", is_last_open_time_ms_exclusive()),
            ("timestamp", "in", required_times),
        ],
    )
    frame = table.to_pandas().sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    if not frame.empty and not frame["timestamp"].isin(required_times).all():
        raise ResidualAbsorptionDataError("event-window reader returned an unrequested minute")
    return frame


__all__ = [
    "ResidualAbsorptionDataError",
    "is_last_open_time_ms_exclusive",
    "read_is_event_window_minutes",
    "read_is_parquet_schema",
    "read_is_symbol_minutes",
]
