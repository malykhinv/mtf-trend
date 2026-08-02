"""Physical IS-only input boundary for drawdown-ladder research."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow.parquet as pq

from anomaly_science.market_context.sessions import MS_PER_MINUTE
from anomaly_science.strategy.drawdown_ladder.spec import DRAWDOWN_LADDER_RESEARCH_SPLIT


class DrawdownLadderDataError(ValueError):
    """Raised when raw input could cross the locked IS boundary."""


def last_open_time_ms_exclusive() -> int:
    """Exclude a minute whose close would land exactly on the OOS boundary."""

    return DRAWDOWN_LADDER_RESEARCH_SPLIT.oos_start_time_ms - MS_PER_MINUTE


def read_is_symbol_minutes(
    path: str | Path,
    *,
    columns: Iterable[str] = ("open", "high", "low", "close"),
) -> pd.DataFrame:
    requested = tuple(dict.fromkeys(("timestamp", *columns)))
    table = pq.read_table(
        Path(path),
        columns=list(requested),
        filters=[
            ("timestamp", ">=", DRAWDOWN_LADDER_RESEARCH_SPLIT.is_start_time_ms),
            ("timestamp", "<", last_open_time_ms_exclusive()),
        ],
    )
    frame = table.to_pandas()
    missing = sorted(set(requested) - set(frame.columns))
    if missing:
        raise DrawdownLadderDataError(f"IS minute input is missing columns: {missing}")
    if frame.empty:
        return frame.loc[:, requested].copy()
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="raise").astype("int64")
    if bool(frame["timestamp"].lt(DRAWDOWN_LADDER_RESEARCH_SPLIT.is_start_time_ms).any()):
        raise DrawdownLadderDataError("reader returned a row before registered IS")
    if bool(frame["timestamp"].ge(last_open_time_ms_exclusive()).any()):
        raise DrawdownLadderDataError("reader returned a row whose close could reach OOS")
    if bool(frame["timestamp"].duplicated().any()):
        raise DrawdownLadderDataError("symbol minute timestamps must be unique")
    return frame.sort_values("timestamp", kind="mergesort").reset_index(drop=True)


__all__ = [
    "DrawdownLadderDataError",
    "last_open_time_ms_exclusive",
    "read_is_symbol_minutes",
]
