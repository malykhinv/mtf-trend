from __future__ import annotations

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

from anomaly_science.artifacts.manifest import build_manifest, write_manifest
from anomaly_science.binance_vision_cache import VisionBlock, make_archive_url
from anomaly_science.market_context.config import ReferenceMarketSpec


REFERENCE_METRICS_SCHEMA_VERSION = "binance_reference_positioning_metrics_5m_v1"
REFERENCE_METRICS_COLUMNS = (
    "toptrader_account_long_short_ratio",
    "toptrader_position_long_short_ratio",
    "global_account_long_short_ratio",
    "taker_long_short_volume_ratio",
)


@dataclass(frozen=True, slots=True)
class ReferenceMetricsArchiveConfig:
    start_date: date
    end_date: date
    references: tuple[ReferenceMarketSpec, ...]
    output_dir: Path
    workers: int = 8
    timeout_seconds: int = 30
    retries: int = 2
    publication_lag_minutes: int = 5

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise ValueError("reference metrics dates are reversed")
        if not self.references:
            raise ValueError("reference metrics require at least one symbol")
        if not 1 <= self.workers <= 16:
            raise ValueError("reference metrics workers must be in 1..16")
        if self.timeout_seconds <= 0 or self.retries < 0:
            raise ValueError("reference metrics timeout/retries are invalid")
        if self.publication_lag_minutes < 5:
            raise ValueError("publication_lag_minutes must be at least one 5m metrics interval")


def run_reference_metrics_archive_build(config: ReferenceMetricsArchiveConfig) -> Path:
    if config.output_dir.exists() and any(config.output_dir.iterdir()):
        raise ValueError(f"reference metrics output must be absent or empty: {config.output_dir}")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    days = _date_range(config.start_date, config.end_date)
    jobs = [(reference, day) for reference in config.references for day in days]
    outcomes: list[dict[str, object]] = []
    frames: dict[str, list[pd.DataFrame]] = {reference.symbol: [] for reference in config.references}
    with ThreadPoolExecutor(max_workers=config.workers) as executor:
        futures = {
            executor.submit(_download_reference_day, reference, day, config): (reference, day)
            for reference, day in jobs
        }
        for future in as_completed(futures):
            reference, day = futures[future]
            frame, status, detail = future.result()
            outcomes.append(
                {
                    "symbol": reference.symbol,
                    "date": day.isoformat(),
                    "status": status,
                    "row_count": len(frame),
                    "detail": detail,
                }
            )
            if not frame.empty:
                frames[reference.symbol].append(frame)
    written: list[Path] = []
    for reference in config.references:
        parts = frames[reference.symbol]
        if not parts:
            raise ValueError(f"no reference metrics downloaded for {reference.symbol}")
        combined = pd.concat(parts, ignore_index=True).sort_values(
            "source_time_ms", kind="mergesort"
        )
        combined = combined.drop_duplicates("source_time_ms", keep="last").reset_index(drop=True)
        if not combined["source_time_ms"].is_monotonic_increasing:
            raise AssertionError("reference metrics timestamps are not increasing")
        path = config.output_dir / f"{reference.symbol}.parquet"
        _write_parquet(path, combined)
        written.append(path)
    coverage = pd.DataFrame(outcomes).sort_values(["symbol", "date"], kind="mergesort")
    coverage_path = config.output_dir / "reference_metrics_coverage.csv"
    _write_csv(coverage_path, coverage)
    written.append(coverage_path)
    metadata_path = config.output_dir / "reference_metrics.metadata.json"
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "schema_version": REFERENCE_METRICS_SCHEMA_VERSION,
        "config": {
            **asdict(config),
            "start_date": config.start_date.isoformat(),
            "end_date": config.end_date.isoformat(),
            "output_dir": str(config.output_dir.resolve()),
        },
        "requested_day_count": len(jobs),
        "ok_day_count": int((coverage["status"] == "ok").sum()),
        "missing_day_count": int((coverage["status"] == "missing").sum()),
        "failed_day_count": int((coverage["status"] == "failed").sum()),
        "temporal_contract": (
            f"available_time_ms = source_time_ms + {config.publication_lag_minutes}m; "
            "downstream joins must be backward as-of only"
        ),
    }
    _write_json(metadata_path, metadata)
    written.append(metadata_path)
    if metadata["failed_day_count"]:
        raise ValueError(f"reference metrics had failed downloads; inspect {coverage_path}")
    manifest = build_manifest(
        run_id="reference-metrics-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=written, root=config.output_dir,
    )
    write_manifest(config.output_dir / "reference_metrics.manifest.json", manifest)
    return config.output_dir


def _download_reference_day(
    reference: ReferenceMarketSpec,
    day: date,
    config: ReferenceMetricsArchiveConfig,
) -> tuple[pd.DataFrame, str, str]:
    block = VisionBlock("daily", day.isoformat(), day, day)
    url = make_archive_url(symbol=reference.symbol, block=block, dataset="metrics")
    response: requests.Response | None = None
    for attempt in range(config.retries + 1):
        try:
            response = requests.get(url, timeout=config.timeout_seconds)
        except requests.RequestException as exc:
            if attempt == config.retries:
                return _empty_metrics(), "failed", f"{type(exc).__name__}: {exc}"
            time.sleep(0.25 * (2**attempt))
            continue
        if response.status_code == 404:
            return _empty_metrics(), "missing", "HTTP 404"
        if response.ok:
            try:
                parsed = parse_metrics_zip(
                    response.content,
                    symbol=reference.symbol,
                    alias=reference.alias,
                    publication_lag_minutes=config.publication_lag_minutes,
                )
                invalid = int(parsed.attrs.get("invalid_ratio_row_count", 0))
                detail = f"dropped_invalid_ratio_rows={invalid}" if invalid else ""
                return parsed, "ok", detail
            except (ValueError, zipfile.BadZipFile) as exc:
                return _empty_metrics(), "failed", f"parse error: {exc}"
        if attempt == config.retries:
            return _empty_metrics(), "failed", f"HTTP {response.status_code}"
        time.sleep(0.25 * (2**attempt))
    return _empty_metrics(), "failed", "unreachable retry state"


def parse_metrics_zip(
    content: bytes,
    *,
    symbol: str,
    alias: str,
    publication_lag_minutes: int,
) -> pd.DataFrame:
    if not symbol or not alias:
        raise ValueError("metrics parser requires symbol and alias")
    if publication_lag_minutes < 5:
        raise ValueError("metrics parser publication lag must be at least five minutes")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(f"expected one metrics CSV, observed {csv_names}")
        frame = pd.read_csv(archive.open(csv_names[0]))
    required = {
        "create_time",
        "count_toptrader_long_short_ratio",
        "sum_toptrader_long_short_ratio",
        "count_long_short_ratio",
        "sum_taker_long_short_vol_ratio",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"metrics CSV missing columns: {missing}")
    timestamp = pd.to_datetime(frame["create_time"], utc=True, errors="raise")
    result = pd.DataFrame(
        {
            "source_time_ms": (timestamp.astype("int64") // 1_000_000).astype("int64"),
            "available_time_ms": (
                timestamp.astype("int64") // 1_000_000
                + publication_lag_minutes * 60_000
            ).astype("int64"),
            "symbol": symbol,
            "alias": alias,
            "toptrader_account_long_short_ratio": pd.to_numeric(
                frame["count_toptrader_long_short_ratio"], errors="coerce"
            ),
            "toptrader_position_long_short_ratio": pd.to_numeric(
                frame["sum_toptrader_long_short_ratio"], errors="coerce"
            ),
            "global_account_long_short_ratio": pd.to_numeric(
                frame["count_long_short_ratio"], errors="coerce"
            ),
            "taker_long_short_volume_ratio": pd.to_numeric(
                frame["sum_taker_long_short_vol_ratio"], errors="coerce"
            ),
            "metrics_schema_version": REFERENCE_METRICS_SCHEMA_VERSION,
        }
    )
    ratios = result.loc[:, list(REFERENCE_METRICS_COLUMNS)].to_numpy(dtype=float)
    valid = np.isfinite(ratios).all(axis=1) & (ratios > 0.0).all(axis=1)
    invalid_count = int((~valid).sum())
    result = result.loc[valid].reset_index(drop=True)
    result.attrs["invalid_ratio_row_count"] = invalid_count
    return result


def _parse_metrics_zip(
    content: bytes,
    reference: ReferenceMarketSpec,
    config: ReferenceMetricsArchiveConfig,
) -> pd.DataFrame:
    """Compatibility wrapper for the original reference-archive unit contract."""

    return parse_metrics_zip(
        content,
        symbol=reference.symbol,
        alias=reference.alias,
        publication_lag_minutes=config.publication_lag_minutes,
    )


def _empty_metrics() -> pd.DataFrame:
    return pd.DataFrame(
        columns=(
            "source_time_ms", "available_time_ms", "symbol", "alias",
            *REFERENCE_METRICS_COLUMNS, "metrics_schema_version",
        )
    )


def _date_range(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


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
    "REFERENCE_METRICS_COLUMNS",
    "REFERENCE_METRICS_SCHEMA_VERSION",
    "ReferenceMetricsArchiveConfig",
    "parse_metrics_zip",
    "run_reference_metrics_archive_build",
]
