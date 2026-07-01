from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import time
import zipfile

import numpy as np
import pandas as pd
import requests

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.binance_vision_cache import BINANCE_VISION_BASE_URL
from anomaly_science.market_context.config import EventScopedPerpCrowdingContextConfig
from anomaly_science.market_context.perp_crowding import PERP_CROWDING_ARCHIVE_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class EventScopedPerpCrowdingArchiveConfig:
    output_dir: Path
    context: EventScopedPerpCrowdingContextConfig
    workers: int = 16
    timeout_seconds: int = 30
    retries: int = 2
    resume: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.workers <= 16:
            raise ValueError("perp crowding archive workers must be in 1..16")
        if self.timeout_seconds <= 0 or self.retries < 0:
            raise ValueError("perp crowding archive timeout/retries are invalid")


@dataclass(frozen=True, slots=True)
class EventPerpCrowdingScope:
    symbol: str
    premium_days: tuple[date, ...]
    funding_months: tuple[str, ...]
    available_intervals_ms: tuple[tuple[int, int], ...]


def run_event_scoped_perp_crowding_archive_build(
    *,
    input_paths: tuple[Path, ...],
    config: EventScopedPerpCrowdingArchiveConfig,
    symbol_column: str = "symbol",
    snapshot_time_column: str = "snapshot_time_ms",
    anchor_time_column: str = "ignition_time_ms",
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> Path:
    if not input_paths:
        raise ValueError("perp crowding scope requires input artifacts")
    if config.output_dir.exists() and any(config.output_dir.iterdir()) and not config.resume:
        raise ValueError(f"perp crowding output must be absent or empty: {config.output_dir}")
    symbols_dir = config.output_dir / "symbols"
    symbols_dir.mkdir(parents=True, exist_ok=True)
    scopes = derive_event_perp_crowding_scope(
        input_paths,
        context=config.context,
        symbol_column=symbol_column,
        snapshot_time_column=snapshot_time_column,
        anchor_time_column=anchor_time_column,
    )
    scope_rows = [
        {"symbol": scope.symbol, "dataset": "premiumIndexKlines", "label": day.isoformat()}
        for scope in scopes for day in scope.premium_days
    ] + [
        {"symbol": scope.symbol, "dataset": "fundingRate", "label": month}
        for scope in scopes for month in scope.funding_months
    ]
    scope_path = config.output_dir / "perp_crowding_scope.csv"
    _write_csv(scope_path, pd.DataFrame(scope_rows).sort_values(["symbol", "dataset", "label"]))

    coverage_parts: list[pd.DataFrame] = []
    artifact_paths: list[Path] = [scope_path]
    with ThreadPoolExecutor(max_workers=config.workers) as executor:
        futures = {
            executor.submit(_build_symbol_archive, scope, symbols_dir, config): scope
            for scope in scopes
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            premium_path, funding_path, coverage_path, coverage = future.result()
            artifact_paths.extend((premium_path, funding_path, coverage_path))
            coverage_parts.append(coverage)
            if progress_callback is not None:
                progress_callback(completed, len(scopes), futures[future].symbol)
    coverage = pd.concat(coverage_parts, ignore_index=True).sort_values(
        ["symbol", "dataset", "label"], kind="mergesort"
    )
    coverage_path = config.output_dir / "perp_crowding_coverage.csv"
    _write_csv(coverage_path, coverage)
    artifact_paths.append(coverage_path)
    metadata_path = config.output_dir / "perp_crowding.metadata.json"
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "schema_version": PERP_CROWDING_ARCHIVE_SCHEMA_VERSION,
        "config": {**asdict(config), "output_dir": str(config.output_dir.resolve())},
        "scope_inputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)} for path in input_paths
        ],
        "symbol_count": len(scopes),
        "requested_archive_count": len(scope_rows),
        "ok_archive_count": int((coverage["status"] == "ok").sum()),
        "missing_archive_count": int((coverage["status"] == "missing").sum()),
        "failed_archive_count": int((coverage["status"] == "failed").sum()),
        "retained_premium_row_count": int(
            coverage.loc[coverage["dataset"] == "premiumIndexKlines", "retained_row_count"].sum()
        ),
        "retained_funding_row_count": int(
            coverage.loc[coverage["dataset"] == "fundingRate", "retained_row_count"].sum()
        ),
        "temporal_contract": (
            "premium available at close_time+1ms; funding available at "
            f"calc_time+{config.context.funding_publication_lag_minutes}m; backward as-of only"
        ),
    }
    _write_json(metadata_path, metadata)
    artifact_paths.append(metadata_path)
    if metadata["failed_archive_count"]:
        raise ValueError(f"perp crowding downloads failed; inspect {coverage_path}")
    manifest = build_manifest(
        run_id="event-perp-crowding-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=tuple(sorted(set(artifact_paths))),
        root=config.output_dir,
    )
    write_manifest(config.output_dir / "perp_crowding.manifest.json", manifest)
    return config.output_dir


def derive_event_perp_crowding_scope(
    input_paths: tuple[Path, ...],
    *,
    context: EventScopedPerpCrowdingContextConfig,
    symbol_column: str,
    snapshot_time_column: str,
    anchor_time_column: str,
) -> tuple[EventPerpCrowdingScope, ...]:
    required = {symbol_column, snapshot_time_column, anchor_time_column}
    frames: list[pd.DataFrame] = []
    for path in input_paths:
        frame = pd.read_parquet(path, columns=sorted(required))
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"perp crowding scope input missing columns: {missing}")
        frames.append(frame)
    rows = pd.concat(frames, ignore_index=True).drop_duplicates(
        [symbol_column, snapshot_time_column, anchor_time_column]
    )
    premium_history_ms = (
        context.premium_zscore_window_minutes + context.premium_max_age_minutes
    ) * 60_000
    funding_history_ms = (
        context.funding_mean_observations * 8 * 60 + context.funding_max_age_minutes
    ) * 60_000
    scopes: list[EventPerpCrowdingScope] = []
    for raw_symbol, group in rows.groupby(symbol_column, sort=True):
        symbol = str(raw_symbol)
        if not symbol or not symbol.replace("_", "").isalnum():
            raise ValueError(f"invalid Binance perp symbol: {symbol}")
        snapshots = pd.to_numeric(group[snapshot_time_column], errors="raise").to_numpy(np.int64)
        anchors = pd.to_numeric(group[anchor_time_column], errors="raise").to_numpy(np.int64)
        if np.any(anchors > snapshots):
            raise ValueError(f"perp crowding anchor exceeds snapshot for {symbol}")
        intervals = _merge_intervals(
            tuple(
                (int(min(anchor, snapshot) - premium_history_ms), int(snapshot))
                for anchor, snapshot in zip(anchors, snapshots, strict=True)
            )
        )
        premium_days: set[date] = set()
        funding_months: set[str] = set()
        for start, end in intervals:
            start_day = pd.Timestamp(start, unit="ms", tz="UTC").date()
            end_day = pd.Timestamp(end, unit="ms", tz="UTC").date()
            premium_days.update(_date_range(start_day, end_day))
            funding_start = pd.Timestamp(start - funding_history_ms, unit="ms", tz="UTC").date()
            cursor = date(funding_start.year, funding_start.month, 1)
            last = date(end_day.year, end_day.month, 1)
            while cursor <= last:
                funding_months.add(cursor.strftime("%Y-%m"))
                cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        scopes.append(
            EventPerpCrowdingScope(
                symbol=symbol,
                premium_days=tuple(sorted(premium_days)),
                funding_months=tuple(sorted(funding_months)),
                available_intervals_ms=intervals,
            )
        )
    return tuple(scopes)


def parse_premium_index_zip(content: bytes, *, symbol: str) -> pd.DataFrame:
    frame = _read_single_csv(content)
    required = {"open_time", "close_time", "close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"premium-index CSV missing columns: {missing}")
    open_time = pd.to_numeric(frame["open_time"], errors="raise").astype("int64")
    close_time = pd.to_numeric(frame["close_time"], errors="raise").astype("int64")
    close = pd.to_numeric(frame["close"], errors="coerce")
    valid = close.notna() & np.isfinite(close.to_numpy(float)) & (close_time >= open_time)
    result = pd.DataFrame(
        {
            "symbol": symbol,
            "premium_source_time_ms": open_time[valid].to_numpy(np.int64),
            "premium_available_time_ms": (close_time[valid] + 1).to_numpy(np.int64),
            "premium_close": close[valid].to_numpy(float),
            "archive_schema_version": PERP_CROWDING_ARCHIVE_SCHEMA_VERSION,
        }
    )
    return result.sort_values("premium_available_time_ms").drop_duplicates(
        "premium_available_time_ms", keep="last"
    ).reset_index(drop=True)


def parse_funding_rate_zip(
    content: bytes,
    *,
    symbol: str,
    publication_lag_minutes: int,
) -> pd.DataFrame:
    frame = _read_single_csv(content)
    required = {"calc_time", "funding_interval_hours", "last_funding_rate"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"funding-rate CSV missing columns: {missing}")
    source = pd.to_numeric(frame["calc_time"], errors="raise").astype("int64")
    interval = pd.to_numeric(frame["funding_interval_hours"], errors="coerce")
    rate = pd.to_numeric(frame["last_funding_rate"], errors="coerce")
    valid = rate.notna() & interval.notna() & np.isfinite(rate.to_numpy(float)) & (interval > 0)
    result = pd.DataFrame(
        {
            "symbol": symbol,
            "funding_source_time_ms": source[valid].to_numpy(np.int64),
            "funding_available_time_ms": (
                source[valid] + publication_lag_minutes * 60_000
            ).to_numpy(np.int64),
            "funding_rate": rate[valid].to_numpy(float),
            "funding_interval_hours": interval[valid].to_numpy(float),
            "archive_schema_version": PERP_CROWDING_ARCHIVE_SCHEMA_VERSION,
        }
    )
    return result.sort_values("funding_available_time_ms").drop_duplicates(
        "funding_available_time_ms", keep="last"
    ).reset_index(drop=True)


def _build_symbol_archive(
    scope: EventPerpCrowdingScope,
    symbols_dir: Path,
    config: EventScopedPerpCrowdingArchiveConfig,
) -> tuple[Path, Path, Path, pd.DataFrame]:
    premium_path = symbols_dir / f"{scope.symbol}.premium.parquet"
    funding_path = symbols_dir / f"{scope.symbol}.funding.parquet"
    coverage_path = symbols_dir / f"{scope.symbol}.coverage.csv"
    expected = {
        *(('premiumIndexKlines', day.isoformat()) for day in scope.premium_days),
        *(('fundingRate', month) for month in scope.funding_months),
    }
    if config.resume and premium_path.is_file() and funding_path.is_file() and coverage_path.is_file():
        coverage = pd.read_csv(coverage_path, dtype={"label": str})
        observed = set(zip(coverage["dataset"].astype(str), coverage["label"].astype(str)))
        if observed != expected:
            raise ValueError(f"perp crowding resume scope mismatch for {scope.symbol}")
        if not (coverage["status"] == "failed").any():
            return premium_path, funding_path, coverage_path, coverage

    premium_parts: list[pd.DataFrame] = []
    funding_parts: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, object]] = []
    with requests.Session() as session:
        for day in scope.premium_days:
            url = _premium_url(scope.symbol, day)
            frame, status, detail = _download_parse(
                session, url, config,
                lambda content: parse_premium_index_zip(content, symbol=scope.symbol),
            )
            retained = _filter_intervals(frame, scope.available_intervals_ms, "premium_available_time_ms")
            coverage_rows.append(_coverage_row(scope.symbol, "premiumIndexKlines", day.isoformat(), status, len(frame), len(retained), detail))
            if not retained.empty:
                premium_parts.append(retained)
        for month in scope.funding_months:
            url = _funding_url(scope.symbol, month)
            frame, status, detail = _download_parse(
                session, url, config,
                lambda content: parse_funding_rate_zip(
                    content,
                    symbol=scope.symbol,
                    publication_lag_minutes=config.context.funding_publication_lag_minutes,
                ),
            )
            expanded = tuple(
                (
                    start - config.context.funding_mean_observations * 8 * 60 * 60_000,
                    end,
                )
                for start, end in scope.available_intervals_ms
            )
            retained = _filter_intervals(frame, expanded, "funding_available_time_ms")
            coverage_rows.append(_coverage_row(scope.symbol, "fundingRate", month, status, len(frame), len(retained), detail))
            if not retained.empty:
                funding_parts.append(retained)
    premium = _combine(premium_parts, _empty_premium(), "premium_available_time_ms")
    funding = _combine(funding_parts, _empty_funding(), "funding_available_time_ms")
    coverage = pd.DataFrame(coverage_rows).sort_values(["dataset", "label"], kind="mergesort")
    _write_parquet(premium_path, premium)
    _write_parquet(funding_path, funding)
    _write_csv(coverage_path, coverage)
    return premium_path, funding_path, coverage_path, coverage


def _download_parse(
    session: requests.Session,
    url: str,
    config: EventScopedPerpCrowdingArchiveConfig,
    parser: Callable[[bytes], pd.DataFrame],
) -> tuple[pd.DataFrame, str, str]:
    response: requests.Response | None = None
    for attempt in range(config.retries + 1):
        try:
            response = session.get(url, timeout=config.timeout_seconds)
        except requests.RequestException as exc:
            if attempt == config.retries:
                return pd.DataFrame(), "failed", f"{type(exc).__name__}: {exc}"
            time.sleep(0.25 * (2**attempt))
            continue
        if response.status_code == 404:
            return pd.DataFrame(), "missing", "HTTP 404"
        if response.ok:
            try:
                return parser(response.content), "ok", ""
            except (ValueError, zipfile.BadZipFile) as exc:
                return pd.DataFrame(), "failed", f"parse error: {exc}"
        if attempt == config.retries:
            return pd.DataFrame(), "failed", f"HTTP {response.status_code}"
        time.sleep(0.25 * (2**attempt))
    return pd.DataFrame(), "failed", "unreachable retry state"


def _read_single_csv(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in archive, observed {names}")
        return pd.read_csv(archive.open(names[0]))


def _premium_url(symbol: str, day: date) -> str:
    label = day.isoformat()
    return (
        f"{BINANCE_VISION_BASE_URL}/data/futures/um/daily/premiumIndexKlines/"
        f"{symbol}/1m/{symbol}-1m-{label}.zip"
    )


def _funding_url(symbol: str, month: str) -> str:
    return (
        f"{BINANCE_VISION_BASE_URL}/data/futures/um/monthly/fundingRate/"
        f"{symbol}/{symbol}-fundingRate-{month}.zip"
    )


def _filter_intervals(frame: pd.DataFrame, intervals: tuple[tuple[int, int], ...], column: str) -> pd.DataFrame:
    if frame.empty or column not in frame:
        return frame
    values = pd.to_numeric(frame[column], errors="raise").to_numpy(np.int64)
    keep = np.zeros(len(frame), dtype=bool)
    for start, end in intervals:
        keep |= (values >= start) & (values <= end)
    return frame.loc[keep].reset_index(drop=True)


def _merge_intervals(intervals: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((start, end) for start, end in merged)


def _date_range(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _combine(parts: list[pd.DataFrame], empty: pd.DataFrame, time_column: str) -> pd.DataFrame:
    if not parts:
        return empty
    return pd.concat(parts, ignore_index=True).sort_values(time_column, kind="mergesort").drop_duplicates(
        time_column, keep="last"
    ).reset_index(drop=True)


def _empty_premium() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "symbol", "premium_source_time_ms", "premium_available_time_ms",
        "premium_close", "archive_schema_version",
    ])


def _empty_funding() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "symbol", "funding_source_time_ms", "funding_available_time_ms",
        "funding_rate", "funding_interval_hours", "archive_schema_version",
    ])


def _coverage_row(symbol: str, dataset: str, label: str, status: str, downloaded: int, retained: int, detail: str) -> dict[str, object]:
    return {
        "symbol": symbol, "dataset": dataset, "label": label, "status": status,
        "downloaded_row_count": downloaded, "retained_row_count": retained, "detail": detail,
    }


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
    temporary.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)


__all__ = [
    "EventPerpCrowdingScope",
    "EventScopedPerpCrowdingArchiveConfig",
    "derive_event_perp_crowding_scope",
    "parse_funding_rate_zip",
    "parse_premium_index_zip",
    "run_event_scoped_perp_crowding_archive_build",
]
