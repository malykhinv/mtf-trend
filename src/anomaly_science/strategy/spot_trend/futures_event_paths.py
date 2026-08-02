from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

from .contracts import SpotTrendContractError
from .futures_data import IS_END, IS_START, OOS_START


EVENT_PATH_HORIZONS_MINUTES: tuple[int, ...] = (60, 240, 1_440, 4_320, 7_200)


@dataclass(frozen=True, slots=True)
class EventPathConfig:
    source_dir: Path = Path(".output/market/binance_vision/um_futures/enriched_1m")
    horizons_minutes: tuple[int, ...] = EVENT_PATH_HORIZONS_MINUTES

    def __post_init__(self) -> None:
        if self.horizons_minutes != EVENT_PATH_HORIZONS_MINUTES:
            raise SpotTrendContractError("event path horizons are frozen at 1h/4h/1d/3d/5d")


def _horizon_name(minutes: int) -> str:
    return {60: "1h", 240: "4h", 1_440: "1d", 4_320: "3d", 7_200: "5d"}[minutes]


def _exact_future_window(
    minute: pd.DataFrame,
    timestamp_values: np.ndarray,
    snapshot_ms: int,
    horizon_minutes: int,
) -> pd.DataFrame | None:
    start = int(np.searchsorted(timestamp_values, snapshot_ms, side="left"))
    end = start + horizon_minutes
    if start >= len(timestamp_values) or end > len(timestamp_values):
        return None
    selected = minute.iloc[start:end]
    expected = np.arange(
        snapshot_ms,
        snapshot_ms + horizon_minutes * 60_000,
        60_000,
        dtype=np.int64,
    )
    if not np.array_equal(selected["timestamp"].to_numpy(dtype=np.int64), expected):
        return None
    return selected


def build_event_future_paths(
    breakout_events: pd.DataFrame,
    daily_bars: pd.DataFrame,
    membership_rows: pd.DataFrame,
    *,
    config: EventPathConfig = EventPathConfig(),
) -> pd.DataFrame:
    """Attach strictly post-snapshot minute paths to point-in-time member breakouts."""

    required_events = {"date", "symbol", "breakout_threshold", "breakout_timestamp"}
    missing_events = sorted(required_events.difference(breakout_events.columns))
    if missing_events:
        raise SpotTrendContractError(f"breakout events missing path anchors: {missing_events}")
    member_keys = membership_rows[["date", "symbol"]].drop_duplicates()
    events = breakout_events.merge(member_keys, on=["date", "symbol"], how="inner", validate="one_to_one")
    market = daily_bars[["date", "symbol", "close", "available_time_ms"]].rename(
        columns={"close": "snapshot_close"}
    )
    events = events.merge(market, on=["date", "symbol"], how="left", validate="one_to_one")
    if events[["snapshot_close", "available_time_ms"]].isna().any().any():
        raise SpotTrendContractError("breakout path event is missing its daily market snapshot")
    is_start_ms = int(pd.Timestamp(IS_START, tz="UTC").timestamp() * 1_000)
    oos_start_ms = int(pd.Timestamp(OOS_START, tz="UTC").timestamp() * 1_000)
    outputs: list[dict[str, object]] = []
    for symbol, symbol_events in events.groupby("symbol", sort=True):
        path = config.source_dir / f"{symbol}.parquet"
        if not path.exists():
            raise FileNotFoundError(path)
        minute = (
            pl.scan_parquet(path)
            .filter((pl.col("timestamp") >= is_start_ms) & (pl.col("timestamp") < oos_start_ms))
            .select("timestamp", "open", "high", "low", "close")
            .sort("timestamp")
            .collect()
            .to_pandas()
        )
        timestamp_values = minute["timestamp"].to_numpy(dtype=np.int64)
        for event in symbol_events.itertuples(index=False):
            snapshot_ms = int(event.available_time_ms)
            if snapshot_ms >= oos_start_ms:
                continue
            row = event._asdict()
            row["snapshot_time"] = pd.to_datetime(snapshot_ms, unit="ms", utc=True)
            row["feature_cutoff_time"] = row["snapshot_time"]
            row["future_start_time"] = row["snapshot_time"] + pd.Timedelta(minutes=1)
            anchor = float(event.snapshot_close)
            threshold = float(event.breakout_threshold)
            for horizon_minutes in config.horizons_minutes:
                name = _horizon_name(horizon_minutes)
                future_end_ms = snapshot_ms + horizon_minutes * 60_000
                row[f"future_end_time_{name}"] = pd.to_datetime(future_end_ms, unit="ms", utc=True)
                if future_end_ms > oos_start_ms:
                    row[f"path_resolved_{name}"] = False
                    continue
                path_frame = _exact_future_window(
                    minute,
                    timestamp_values,
                    snapshot_ms,
                    horizon_minutes,
                )
                row[f"path_resolved_{name}"] = path_frame is not None
                if path_frame is None:
                    continue
                closes = path_frame["close"].to_numpy(dtype=float)
                highs = path_frame["high"].to_numpy(dtype=float)
                lows = path_frame["low"].to_numpy(dtype=float)
                log_path = np.diff(np.log(np.concatenate(([anchor], closes))))
                total_path = float(np.abs(log_path).sum())
                forward_return = float(np.log(closes[-1] / anchor))
                failure_positions = np.flatnonzero(closes <= threshold)
                row[f"forward_return_{name}"] = forward_return
                row[f"mfe_{name}"] = float(highs.max() / anchor - 1.0)
                row[f"mae_{name}"] = float(lows.min() / anchor - 1.0)
                row[f"realized_variance_{name}"] = float(np.square(log_path).sum())
                row[f"path_efficiency_{name}"] = abs(forward_return) / total_path if total_path > 0 else np.nan
                row[f"level_margin_{name}"] = float(closes[-1] / threshold - 1.0)
                row[f"level_retained_{name}"] = bool(closes[-1] > threshold)
                row[f"failed_level_within_{name}"] = bool(len(failure_positions))
                row[f"time_to_level_failure_minutes_{name}"] = (
                    float(failure_positions[0] + 1) if len(failure_positions) else np.nan
                )
                row[f"time_to_mfe_minutes_{name}"] = float(np.argmax(highs) + 1)
                row[f"time_to_mae_minutes_{name}"] = float(np.argmin(lows) + 1)
                risk = max(float(np.sqrt(np.square(log_path).sum())), 0.01)
                row[f"continuation_score_{name}"] = forward_return / risk
            outputs.append(row)
    result = pd.DataFrame(outputs)
    if result.empty:
        raise SpotTrendContractError("event future-path builder produced no IS events")
    resolved_columns = [f"path_resolved_{_horizon_name(value)}" for value in config.horizons_minutes]
    if not result[resolved_columns].any(axis=None):
        raise SpotTrendContractError("event future-path builder resolved no causal horizons")
    resolved = result[resolved_columns].any(axis=1)
    invalid_time = resolved & (
        result["feature_cutoff_time"].gt(result["snapshot_time"])
        | result["future_start_time"].le(result["snapshot_time"])
    )
    if invalid_time.any():
        raise SpotTrendContractError("event path rows violate features <= snapshot < labels")
    return result.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def event_path_data_audit(paths: pd.DataFrame) -> dict[str, object]:
    audit: dict[str, object] = {
        "schema_version": "usdm_breakout_future_paths_v1_is_2025",
        "is_start": IS_START.isoformat(),
        "is_end_inclusive": IS_END.isoformat(),
        "oos_start_locked": OOS_START.isoformat(),
        "event_rows": len(paths),
        "event_symbols": paths["symbol"].nunique(),
        "first_snapshot": paths["snapshot_time"].min().isoformat(),
        "last_snapshot": paths["snapshot_time"].max().isoformat(),
    }
    for minutes in EVENT_PATH_HORIZONS_MINUTES:
        name = _horizon_name(minutes)
        audit[f"resolved_{name}"] = int(paths[f"path_resolved_{name}"].sum())
    return audit


def build_causal_crossing_future_paths(
    crossings: pd.DataFrame,
    *,
    config: EventPathConfig = EventPathConfig(),
) -> pd.DataFrame:
    """Build paths after the close of each online-detectable crossing minute."""

    required = {
        "event_id",
        "symbol",
        "snapshot_time",
        "feature_cutoff_time",
        "snapshot_close",
        "breakout_threshold",
    }
    missing = sorted(required.difference(crossings.columns))
    if missing:
        raise SpotTrendContractError(f"causal crossing rows missing path fields: {missing}")
    oos_start_ms = int(pd.Timestamp(OOS_START, tz="UTC").timestamp() * 1_000)
    is_start_ms = int(pd.Timestamp(IS_START, tz="UTC").timestamp() * 1_000)
    outputs: list[dict[str, object]] = []
    for symbol, symbol_events in crossings.groupby("symbol", sort=True):
        path = config.source_dir / f"{symbol}.parquet"
        if not path.exists():
            raise FileNotFoundError(path)
        minute = (
            pl.scan_parquet(path)
            .filter((pl.col("timestamp") >= is_start_ms) & (pl.col("timestamp") < oos_start_ms))
            .select("timestamp", "open", "high", "low", "close")
            .sort("timestamp")
            .collect()
            .to_pandas()
        )
        timestamp_values = minute["timestamp"].to_numpy(dtype=np.int64)
        for event in symbol_events.itertuples(index=False):
            snapshot_ms = int(pd.Timestamp(event.snapshot_time).timestamp() * 1_000)
            if snapshot_ms >= oos_start_ms:
                continue
            row = event._asdict()
            row["future_start_time"] = pd.to_datetime(snapshot_ms + 60_000, unit="ms", utc=True)
            anchor = float(event.snapshot_close)
            threshold = float(event.breakout_threshold)
            for horizon_minutes in config.horizons_minutes:
                name = _horizon_name(horizon_minutes)
                future_end_ms = snapshot_ms + horizon_minutes * 60_000
                row[f"future_end_time_{name}"] = pd.to_datetime(future_end_ms, unit="ms", utc=True)
                if future_end_ms > oos_start_ms:
                    row[f"path_resolved_{name}"] = False
                    continue
                path_frame = _exact_future_window(
                    minute,
                    timestamp_values,
                    snapshot_ms,
                    horizon_minutes,
                )
                row[f"path_resolved_{name}"] = path_frame is not None
                if path_frame is None:
                    continue
                closes = path_frame["close"].to_numpy(dtype=float)
                highs = path_frame["high"].to_numpy(dtype=float)
                lows = path_frame["low"].to_numpy(dtype=float)
                log_path = np.diff(np.log(np.concatenate(([anchor], closes))))
                total_path = float(np.abs(log_path).sum())
                forward_return = float(np.log(closes[-1] / anchor))
                failure_positions = np.flatnonzero(closes <= threshold)
                realized_variance = float(np.square(log_path).sum())
                row[f"forward_return_{name}"] = forward_return
                row[f"mfe_{name}"] = float(highs.max() / anchor - 1.0)
                row[f"mae_{name}"] = float(lows.min() / anchor - 1.0)
                row[f"realized_variance_{name}"] = realized_variance
                row[f"path_efficiency_{name}"] = abs(forward_return) / total_path if total_path > 0 else np.nan
                row[f"level_margin_{name}"] = float(closes[-1] / threshold - 1.0)
                row[f"level_retained_{name}"] = bool(closes[-1] > threshold)
                row[f"failed_level_within_{name}"] = bool(len(failure_positions))
                row[f"time_to_level_failure_minutes_{name}"] = (
                    float(failure_positions[0] + 1) if len(failure_positions) else np.nan
                )
                row[f"time_to_mfe_minutes_{name}"] = float(np.argmax(highs) + 1)
                row[f"time_to_mae_minutes_{name}"] = float(np.argmin(lows) + 1)
                row[f"continuation_score_{name}"] = forward_return / max(np.sqrt(realized_variance), 0.01)
            outputs.append(row)
    result = pd.DataFrame(outputs)
    if result.empty:
        raise SpotTrendContractError("causal crossing path builder produced no IS rows")
    invalid = result["feature_cutoff_time"].gt(result["snapshot_time"]) | result["future_start_time"].le(
        result["snapshot_time"]
    )
    if invalid.any():
        raise SpotTrendContractError("causal crossing rows violate features <= snapshot < labels")
    return result.sort_values(["snapshot_time", "symbol"], kind="stable").reset_index(drop=True)
