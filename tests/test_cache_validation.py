from __future__ import annotations

import json

import pandas as pd
import pytest

from anomaly_science.cache_validation import CacheExportProofValidationConfig, validate_cache_export_proof
from anomaly_science.cli import build_parser


def test_cache_export_proof_validation_classifies_settlement_transition_gap(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    manifest_path = tmp_path / "cache_export_manifest.json"
    coverage_path = tmp_path / "cache_export_coverage.csv"
    out_path = tmp_path / "cache_export_validation.json"

    pd.DataFrame(
        {
            "timestamp": [1704067200000, 1704067260000, 1704067440000],
            "open": [1, 2, 3],
        }
    ).to_parquet(cache_dir / "GAPUSDT.parquet")
    pd.DataFrame({"timestamp": [1704067320000]}).to_parquet(cache_dir / "GAPUSDTSETTLED.parquet")
    pd.DataFrame(
        [
            {
                "symbol": "GAPUSDT",
                "rows_1m": 3,
                "unique_rows_1m": 3,
                "expected_rows_1m_by_span": 5,
                "missing_rows_1m_by_span": 2,
                "duplicate_rows_1m": 0,
                "non_1m_step_count": 1,
                "max_gap_minutes": 2,
                "first_gap_after_open_time_ms": 1704067260000,
                "first_open_time_ms": 1704067200000,
                "last_open_time_ms": 1704067440000,
                "first_date": "2024-01-01",
                "last_date": "2024-01-01",
                "calendar_days": 1,
                "observed_utc_days": 1,
                "missing_utc_days": 0,
                "partial_utc_days": 1,
                "has_open_interest": True,
                "has_complete_1m_span": False,
                "source_path": str(cache_dir / "GAPUSDT.parquet"),
            }
        ]
    ).to_csv(coverage_path, index=False)
    manifest_path.write_text(
        json.dumps(
            {
                "cache_dir": str(cache_dir),
                "effective_start_date": "2024-01-01",
                "effective_end_date": "2024-01-01",
                "effective_calendar_days": 1,
                "rows_1m": 3,
                "excluded_delivery_contract_symbols": [],
            }
        ),
        encoding="utf-8",
    )

    validate_cache_export_proof(
        CacheExportProofValidationConfig(
            manifest_path=manifest_path,
            coverage_path=coverage_path,
            out_path=out_path,
            expected_days=1,
            allow_settlement_transition_gaps=True,
        )
    )

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["validation_passed"] is True
    assert payload["missing_1m_rows_total"] == 2
    assert payload["settlement_transition_missing_1m_rows"] == 2
    assert payload["unclassified_missing_1m_rows"] == 0
    assert payload["gap_details"][0]["classification"] == "settlement_transition"


def test_cache_export_proof_validation_fails_unclassified_gap(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    manifest_path = tmp_path / "cache_export_manifest.json"
    coverage_path = tmp_path / "cache_export_coverage.csv"
    out_path = tmp_path / "cache_export_validation.json"

    pd.DataFrame({"timestamp": [1704067200000, 1704067380000]}).to_parquet(cache_dir / "GAPUSDT.parquet")
    pd.DataFrame(
        [
            {
                "symbol": "GAPUSDT",
                "missing_rows_1m_by_span": 2,
                "missing_utc_days": 0,
                "duplicate_rows_1m": 0,
            }
        ]
    ).to_csv(coverage_path, index=False)
    manifest_path.write_text(
        json.dumps({"cache_dir": str(cache_dir), "effective_calendar_days": 1, "rows_1m": 2}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unclassified_missing_1m_rows_present"):
        validate_cache_export_proof(
            CacheExportProofValidationConfig(
                manifest_path=manifest_path,
                coverage_path=coverage_path,
                out_path=out_path,
                expected_days=1,
            )
        )

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["validation_passed"] is False
    assert payload["unclassified_missing_1m_rows"] == 2


def test_validate_cache_export_proof_cli_accepts_strict_flags() -> None:
    args = build_parser().parse_args(
        [
            "validate-cache-export-proof",
            "--manifest",
            "tmp/mvp1_input_380d/cache_export_manifest.json",
            "--coverage",
            "tmp/mvp1_input_380d/cache_export_coverage.csv",
            "--out",
            "research/validation/cache_export_380d_validation.json",
            "--expected-days",
            "380",
            "--allow-settlement-transition-gaps",
        ]
    )

    assert args.command == "validate-cache-export-proof"
    assert args.expected_days == 380
    assert args.allow_settlement_transition_gaps is True
    assert args.allow_missing_utc_days is False
    assert args.allow_unclassified_1m_gaps is False
