from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from collections.abc import Callable
import zipfile

import numpy as np
import pandas as pd
import requests

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.binance_vision_cache import VisionBlock, make_archive_url
from anomaly_science.market_context.config import EventScopedPositioningContextConfig
from anomaly_science.market_context.metrics_archive import (
    REFERENCE_METRICS_COLUMNS,
    parse_metrics_zip,
)


EVENT_SCOPED_METRICS_ARCHIVE_SCHEMA_VERSION = "event_scoped_positioning_archive_v1"


@dataclass(frozen=True, slots=True)
class EventScopedMetricsArchiveConfig:
    output_dir: Path
    positioning: EventScopedPositioningContextConfig
    workers: int = 16
    timeout_seconds: int = 30
    retries: int = 2
    resume: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.workers <= 16:
            raise ValueError("event metrics workers must be in 1..16")
        if self.timeout_seconds <= 0 or self.retries < 0:
            raise ValueError("event metrics timeout/retries are invalid")


@dataclass(frozen=True, slots=True)
class EventSymbolMetricsScope:
    symbol: str
    days: tuple[date, ...]
    available_intervals_ms: tuple[tuple[int, int], ...]


def run_event_scoped_metrics_archive_build(
    *,
    input_paths: tuple[Path, ...],
    config: EventScopedMetricsArchiveConfig,
    symbol_column: str = "symbol",
    snapshot_time_column: str = "snapshot_time_ms",
    anchor_time_column: str = "ignition_time_ms",
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> Path:
    """Acquire only symbol-days needed by registered event snapshots and history."""

    if not input_paths:
        raise ValueError("event metrics scope requires at least one input artifact")
    if config.output_dir.exists() and any(config.output_dir.iterdir()) and not config.resume:
        raise ValueError(f"event metrics output must be absent or empty: {config.output_dir}")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    symbols_dir = config.output_dir / "symbols"
    symbols_dir.mkdir(parents=True, exist_ok=True)
    scopes = derive_event_metrics_scope(
        input_paths,
        positioning=config.positioning,
        symbol_column=symbol_column,
        snapshot_time_column=snapshot_time_column,
        anchor_time_column=anchor_time_column,
    )
    scope_rows = [
        {"symbol": scope.symbol, "date": day.isoformat()}
        for scope in scopes
        for day in scope.days
    ]
    scope_path = config.output_dir / "event_metrics_scope.csv"
    _write_csv(scope_path, pd.DataFrame(scope_rows))

    outcomes: list[pd.DataFrame] = []
    symbol_paths: list[Path] = []
    coverage_paths: list[Path] = []
    with ThreadPoolExecutor(max_workers=config.workers) as executor:
        futures = {
            executor.submit(_build_symbol_archive, scope, symbols_dir, config): scope
            for scope in scopes
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            scope = futures[future]
            symbol_path, coverage_path, coverage = future.result()
            symbol_paths.append(symbol_path)
            coverage_paths.append(coverage_path)
            outcomes.append(coverage)
            if progress_callback is not None:
                progress_callback(completed, len(scopes), scope.symbol)
    coverage = pd.concat(outcomes, ignore_index=True).sort_values(
        ["symbol", "date"], kind="mergesort"
    )
    coverage_path = config.output_dir / "event_metrics_coverage.csv"
    _write_csv(coverage_path, coverage)
    metadata_path = config.output_dir / "event_metrics.metadata.json"
    failed = int((coverage["status"] == "failed").sum())
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "schema_version": EVENT_SCOPED_METRICS_ARCHIVE_SCHEMA_VERSION,
        "config": {
            **asdict(config),
            "output_dir": str(config.output_dir.resolve()),
        },
        "scope_inputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for path in input_paths
        ],
        "symbol_count": len(scopes),
        "requested_symbol_day_count": len(scope_rows),
        "ok_symbol_day_count": int((coverage["status"] == "ok").sum()),
        "missing_symbol_day_count": int((coverage["status"] == "missing").sum()),
        "failed_symbol_day_count": failed,
        "retained_metrics_row_count": int(coverage["retained_row_count"].sum()),
        "invalid_ratio_row_count": int(coverage["invalid_ratio_row_count"].sum()),
        "temporal_contract": (
            "scope covers registered snapshot/ignition history; "
            "available_time=source_time+publication_lag; downstream join is backward as-of"
        ),
    }
    _write_json(metadata_path, metadata)
    if failed:
        raise ValueError(
            f"event-scoped metrics had failed downloads; inspect {coverage_path}"
        )
    artifacts = (
        scope_path,
        coverage_path,
        metadata_path,
        *sorted(symbol_paths),
        *sorted(coverage_paths),
    )
    manifest = build_manifest(
        run_id="event-scoped-metrics-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=artifacts,
        root=config.output_dir,
    )
    write_manifest(config.output_dir / "event_metrics.manifest.json", manifest)
    return config.output_dir


def derive_event_metrics_scope(
    input_paths: tuple[Path, ...],
    *,
    positioning: EventScopedPositioningContextConfig,
    symbol_column: str,
    snapshot_time_column: str,
    anchor_time_column: str,
) -> tuple[EventSymbolMetricsScope, ...]:
    parts: list[pd.DataFrame] = []
    required = {symbol_column, snapshot_time_column, anchor_time_column}
    for path in input_paths:
        frame = pd.read_parquet(path, columns=sorted(required))
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"event metrics scope input missing columns: {missing}")
        parts.append(frame)
    rows = pd.concat(parts, ignore_index=True).drop_duplicates(
        [symbol_column, snapshot_time_column, anchor_time_column]
    )
    history_ms = (
        max(positioning.change_lags_minutes) + positioning.max_age_minutes
    ) * 60_000
    publication_ms = positioning.publication_lag_minutes * 60_000
    scopes: list[EventSymbolMetricsScope] = []
    for raw_symbol, group in rows.groupby(symbol_column, sort=True):
        symbol = str(raw_symbol)
        _validate_symbol(symbol)
        snapshots = pd.to_numeric(group[snapshot_time_column], errors="raise").to_numpy(
            dtype=np.int64
        )
        anchors = pd.to_numeric(group[anchor_time_column], errors="raise").to_numpy(
            dtype=np.int64
        )
        if np.any(anchors > snapshots):
            raise ValueError(f"event positioning anchor exceeds snapshot for {symbol}")
        intervals = _merge_intervals(
            tuple(
                (
                    int(min(anchor, snapshot) - history_ms),
                    int(snapshot),
                )
                for anchor, snapshot in zip(anchors, snapshots, strict=True)
            )
        )
        days: set[date] = set()
        for start_available, end_available in intervals:
            start_source = start_available - publication_ms
            end_source = end_available - publication_ms
            start_day = pd.Timestamp(start_source, unit="ms", tz="UTC").date()
            end_day = pd.Timestamp(end_source, unit="ms", tz="UTC").date()
            days.update(_date_range(start_day, end_day))
        scopes.append(
            EventSymbolMetricsScope(
                symbol=symbol,
                days=tuple(sorted(days)),
                available_intervals_ms=intervals,
            )
        )
    return tuple(scopes)


def _build_symbol_archive(
    scope: EventSymbolMetricsScope,
    symbols_dir: Path,
    config: EventScopedMetricsArchiveConfig,
) -> tuple[Path, Path, pd.DataFrame]:
    output_path = symbols_dir / f"{scope.symbol}.parquet"
    coverage_path = symbols_dir / f"{scope.symbol}.coverage.csv"
    if config.resume and output_path.is_file() and coverage_path.is_file():
        coverage = pd.read_csv(coverage_path)
        observed_days = tuple(date.fromisoformat(value) for value in coverage["date"])
        if observed_days != scope.days or set(coverage["symbol"].astype(str)) != {scope.symbol}:
            raise ValueError(f"resume scope mismatch for {scope.symbol}")
        if not (coverage["status"] == "failed").any():
            if "invalid_ratio_row_count" not in coverage:
                coverage["invalid_ratio_row_count"] = 0
            _validate_symbol_metrics(
                pd.read_parquet(output_path), scope.symbol, allow_empty=True
            )
            return output_path, coverage_path, coverage

    frames: list[pd.DataFrame] = []
    rows: list[dict[str, object]] = []
    with requests.Session() as session:
        for day in scope.days:
            frame, status, detail = _download_day(
                session,
                symbol=scope.symbol,
                day=day,
                config=config,
            )
            retained = _filter_intervals(frame, scope.available_intervals_ms)
            invalid_ratio_rows = int(frame.attrs.get("invalid_ratio_row_count", 0))
            rows.append(
                {
                    "symbol": scope.symbol,
                    "date": day.isoformat(),
                    "status": status,
                    "downloaded_row_count": len(frame),
                    "retained_row_count": len(retained),
                    "invalid_ratio_row_count": invalid_ratio_rows,
                    "detail": detail,
                }
            )
            if not retained.empty:
                frames.append(retained)
    combined = (
        pd.concat(frames, ignore_index=True)
        .sort_values("available_time_ms", kind="mergesort")
        .drop_duplicates("available_time_ms", keep="last")
        .reset_index(drop=True)
        if frames
        else _empty_symbol_metrics()
    )
    _validate_symbol_metrics(combined, scope.symbol, allow_empty=True)
    coverage = pd.DataFrame(rows)
    _write_parquet(output_path, combined)
    _write_csv(coverage_path, coverage)
    return output_path, coverage_path, coverage


def _download_day(
    session: requests.Session,
    *,
    symbol: str,
    day: date,
    config: EventScopedMetricsArchiveConfig,
) -> tuple[pd.DataFrame, str, str]:
    block = VisionBlock("daily", day.isoformat(), day, day)
    url = make_archive_url(symbol=symbol, block=block, dataset="metrics")
    for attempt in range(config.retries + 1):
        try:
            response = session.get(url, timeout=config.timeout_seconds)
        except requests.RequestException as exc:
            if attempt == config.retries:
                return _empty_symbol_metrics(), "failed", f"{type(exc).__name__}: {exc}"
            time.sleep(0.25 * (2**attempt))
            continue
        if response.status_code == 404:
            return _empty_symbol_metrics(), "missing", "HTTP 404"
        if response.ok:
            try:
                return (
                    parse_metrics_zip(
                        response.content,
                        symbol=symbol,
                        alias="symbol",
                        publication_lag_minutes=(
                            config.positioning.publication_lag_minutes
                        ),
                    ),
                    "ok",
                    "",
                )
            except (ValueError, zipfile.BadZipFile) as exc:
                return _empty_symbol_metrics(), "failed", f"parse error: {exc}"
        if attempt == config.retries:
            return _empty_symbol_metrics(), "failed", f"HTTP {response.status_code}"
        time.sleep(0.25 * (2**attempt))
    return _empty_symbol_metrics(), "failed", "unreachable retry state"


def _filter_intervals(
    frame: pd.DataFrame,
    intervals: tuple[tuple[int, int], ...],
) -> pd.DataFrame:
    if frame.empty:
        return frame
    available = frame["available_time_ms"].to_numpy(dtype=np.int64)
    mask = np.zeros(len(frame), dtype=bool)
    for start, end in intervals:
        mask |= (available >= start) & (available <= end)
    return frame.loc[mask].copy()


def _merge_intervals(
    intervals: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    if not intervals:
        return ()
    ordered = sorted(intervals)
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        prior_start, prior_end = merged[-1]
        if start <= prior_end:
            merged[-1] = (prior_start, max(prior_end, end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _validate_symbol_metrics(
    frame: pd.DataFrame,
    symbol: str,
    *,
    allow_empty: bool = False,
) -> None:
    required = {
        "source_time_ms",
        "available_time_ms",
        "symbol",
        "alias",
        "metrics_schema_version",
        *REFERENCE_METRICS_COLUMNS,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"symbol metrics artifact missing columns: {missing}")
    if frame.empty:
        if allow_empty:
            return
        raise ValueError(f"resumed symbol metrics artifact is empty for {symbol}")
    if set(frame["symbol"].astype(str).unique()) != {symbol}:
        raise ValueError(f"symbol metrics artifact identity mismatch for {symbol}")
    if frame["available_time_ms"].duplicated().any():
        raise ValueError(f"symbol metrics artifact has duplicate availability times: {symbol}")
    if (
        frame["available_time_ms"].to_numpy(dtype=np.int64)
        <= frame["source_time_ms"].to_numpy(dtype=np.int64)
    ).any():
        raise ValueError(f"symbol metrics artifact violates publication lag: {symbol}")


def _empty_symbol_metrics() -> pd.DataFrame:
    return pd.DataFrame(
        columns=(
            "source_time_ms",
            "available_time_ms",
            "symbol",
            "alias",
            *REFERENCE_METRICS_COLUMNS,
            "metrics_schema_version",
        )
    )


def _date_range(start: date, end: date) -> tuple[date, ...]:
    return tuple(
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
    )


def _validate_symbol(symbol: str) -> None:
    if not symbol or not symbol.replace("_", "").isalnum():
        raise ValueError(f"unsafe event metrics symbol: {symbol!r}")


def _write_parquet(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


__all__ = [
    "EVENT_SCOPED_METRICS_ARCHIVE_SCHEMA_VERSION",
    "EventSymbolMetricsScope",
    "EventScopedMetricsArchiveConfig",
    "derive_event_metrics_scope",
    "run_event_scoped_metrics_archive_build",
]
