from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from anomaly_science.cache_export import ONE_MINUTE_MS


@dataclass(frozen=True, slots=True)
class CacheExportProofValidationConfig:
    manifest_path: Path
    coverage_path: Path
    out_path: Path
    expected_days: int = 380
    fail_on_missing_utc_days: bool = True
    fail_on_unclassified_1m_gaps: bool = True
    allow_settlement_transition_gaps: bool = True

    def __post_init__(self) -> None:
        if self.expected_days <= 0:
            raise ValueError("expected_days must be positive")


def validate_cache_export_proof(config: CacheExportProofValidationConfig) -> Path:
    manifest = json.loads(config.manifest_path.read_text(encoding="utf-8"))
    coverage = pd.read_csv(config.coverage_path)
    cache_dir = Path(str(manifest.get("cache_dir", "")))
    if not cache_dir:
        raise ValueError(f"{config.manifest_path} is missing cache_dir")

    gap_details = _classify_1m_gaps(
        coverage=coverage,
        cache_dir=cache_dir,
        allow_settlement_transition_gaps=config.allow_settlement_transition_gaps,
    )
    missing_utc_days_total = int(coverage["missing_utc_days"].sum())
    duplicate_1m_rows_total = int(coverage["duplicate_rows_1m"].sum())
    missing_1m_rows_total = int(coverage["missing_rows_1m_by_span"].sum())
    settlement_transition_missing_rows = sum(item["missing_rows"] for item in gap_details if item["classification"] == "settlement_transition")
    unclassified_missing_rows = sum(item["missing_rows"] for item in gap_details if item["classification"] == "unclassified")
    effective_calendar_days = int(manifest.get("effective_calendar_days") or 0)

    failures: list[str] = []
    if effective_calendar_days < config.expected_days:
        failures.append(
            f"effective_calendar_days_below_expected: expected>={config.expected_days} actual={effective_calendar_days}"
        )
    if duplicate_1m_rows_total:
        failures.append(f"duplicate_1m_rows_present: total={duplicate_1m_rows_total}")
    if config.fail_on_missing_utc_days and missing_utc_days_total:
        failures.append(f"missing_utc_days_present: total={missing_utc_days_total}")
    if config.fail_on_unclassified_1m_gaps and unclassified_missing_rows:
        failures.append(f"unclassified_missing_1m_rows_present: total={unclassified_missing_rows}")

    payload: dict[str, Any] = {
        "validation_version": "cache_export_proof_validation_v1",
        "validation_passed": not failures,
        "validation_failures": failures,
        "manifest_path": str(config.manifest_path),
        "manifest_sha256": _sha256_file(config.manifest_path),
        "coverage_path": str(config.coverage_path),
        "coverage_sha256": _sha256_file(config.coverage_path),
        "cache_dir": str(cache_dir),
        "expected_days": config.expected_days,
        "effective_start_date": manifest.get("effective_start_date"),
        "effective_end_date": manifest.get("effective_end_date"),
        "effective_calendar_days": effective_calendar_days,
        "exported_symbol_count": int(coverage["symbol"].nunique()),
        "rows_1m": int(manifest.get("rows_1m") or 0),
        "excluded_delivery_contract_symbols": manifest.get("excluded_delivery_contract_symbols", []),
        "missing_utc_days_total": missing_utc_days_total,
        "duplicate_1m_rows_total": duplicate_1m_rows_total,
        "missing_1m_rows_total": missing_1m_rows_total,
        "settlement_transition_missing_1m_rows": int(settlement_transition_missing_rows),
        "unclassified_missing_1m_rows": int(unclassified_missing_rows),
        "symbols_with_missing_1m_rows": sorted({item["symbol"] for item in gap_details}),
        "gap_details": gap_details,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    config.out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.out_path.with_suffix(config.out_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(config.out_path)
    if failures:
        raise ValueError("cache export proof validation failed: " + "; ".join(failures))
    return config.out_path


def _classify_1m_gaps(
    *,
    coverage: pd.DataFrame,
    cache_dir: Path,
    allow_settlement_transition_gaps: bool,
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    gap_rows = coverage[coverage["missing_rows_1m_by_span"] > 0]
    for symbol in gap_rows["symbol"].tolist():
        timestamps = _read_symbol_timestamps(cache_dir / f"{symbol}.parquet")
        settled_timestamps = _read_optional_symbol_timestamps(cache_dir / f"{symbol}SETTLED.parquet")
        for previous_ms, next_ms in _timestamp_gaps(timestamps):
            missing_rows = int((next_ms - previous_ms) // ONE_MINUTE_MS - 1)
            classification = "unclassified"
            settled_symbol = ""
            if allow_settlement_transition_gaps and _has_timestamp_between(settled_timestamps, previous_ms, next_ms):
                classification = "settlement_transition"
                settled_symbol = f"{symbol}SETTLED"
            details.append(
                {
                    "symbol": symbol,
                    "previous_open_time_ms": int(previous_ms),
                    "next_open_time_ms": int(next_ms),
                    "previous_open_time_utc": datetime.fromtimestamp(previous_ms / 1000, tz=timezone.utc).isoformat(),
                    "next_open_time_utc": datetime.fromtimestamp(next_ms / 1000, tz=timezone.utc).isoformat(),
                    "missing_rows": missing_rows,
                    "classification": classification,
                    "settled_symbol": settled_symbol,
                }
            )
    return details


def _read_symbol_timestamps(path: Path) -> pd.Series:
    if not path.exists():
        raise FileNotFoundError(f"cache parquet is missing: {path}")
    frame = pd.read_parquet(path, columns=["timestamp"])
    return frame["timestamp"].astype("int64").drop_duplicates().sort_values(ignore_index=True)


def _read_optional_symbol_timestamps(path: Path) -> pd.Series:
    if not path.exists():
        return pd.Series([], dtype="int64")
    return _read_symbol_timestamps(path)


def _timestamp_gaps(timestamps: pd.Series) -> list[tuple[int, int]]:
    if timestamps.empty:
        return []
    gaps: list[tuple[int, int]] = []
    previous = int(timestamps.iloc[0])
    for value in timestamps.iloc[1:].tolist():
        current = int(value)
        if current - previous != ONE_MINUTE_MS:
            gaps.append((previous, current))
        previous = current
    return gaps


def _has_timestamp_between(timestamps: pd.Series, previous_ms: int, next_ms: int) -> bool:
    if timestamps.empty:
        return False
    return bool(((timestamps > previous_ms) & (timestamps < next_ms)).any())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
