from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from anomaly_science.contracts.audit import AuditStatus
from anomaly_science.data import CsvDataSourceError, CsvDirectoryDataSource, run_data_quality, run_mvp1_data_audit
from anomaly_science.universe import build_symbol_universe_by_day


FIXTURE_DIR = Path("tests/fixtures/minimal_market_data")


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def test_csv_source_requires_explicit_quote_volume(tmp_path: Path) -> None:
    source_dir = tmp_path / "data"
    source_dir.mkdir()
    (source_dir / "candles_1m.csv").write_text(
        "symbol,open_time_ms,available_time_ms,open,high,low,close,volume\n"
        "AAA/USDT:USDT,0,60000,1,1.1,0.9,1.0,10\n",
        encoding="utf-8",
    )

    with pytest.raises(CsvDataSourceError, match="quote_volume"):
        CsvDirectoryDataSource(source_dir).read_frame("candles_1m")


def test_data_quality_flags_duplicate_candles() -> None:
    frame = pd.DataFrame(
        [
            {
                "symbol": "AAA/USDT:USDT",
                "open_time_ms": 0,
                "available_time_ms": 60_000,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.0,
                "volume": 1.0,
                "quote_volume": 10.0,
            },
            {
                "symbol": "AAA/USDT:USDT",
                "open_time_ms": 0,
                "available_time_ms": 60_000,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.0,
                "volume": 1.0,
                "quote_volume": 10.0,
            },
        ]
    )

    rows = run_data_quality({"candles_1m": frame, "candles_5m": frame, "open_interest_5m": None, "liquidations": None})
    by_name = {row.check_name: row for row in rows}

    assert by_name["candles_1m_duplicate_symbol_time"].status == AuditStatus.FAIL
    assert by_name["candles_1m_duplicate_symbol_time"].severity == "critical"
    assert by_name["open_interest_5m_present"].status == AuditStatus.WARN


def test_data_quality_marks_first_1m_candle_after_gap_as_technical_noise_shock() -> None:
    frame = pd.DataFrame(
        [
            {
                "symbol": "AAA/USDT:USDT",
                "open_time_ms": 0,
                "available_time_ms": 60_000,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.0,
                "volume": 1.0,
                "quote_volume": 10.0,
            },
            {
                "symbol": "AAA/USDT:USDT",
                "open_time_ms": 4 * 60_000,
                "available_time_ms": 5 * 60_000,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.0,
                "volume": 1.0,
                "quote_volume": 10.0,
            },
        ]
    )

    rows = run_data_quality({"candles_1m": frame, "candles_5m": frame, "open_interest_5m": None, "liquidations": None})
    shock_rows = [row for row in rows if row.check_name == "candles_1m_technical_noise_shock"]

    assert len(shock_rows) == 1
    shock = shock_rows[0]
    assert shock.status == AuditStatus.WARN
    assert shock.symbol == "AAA/USDT:USDT"
    assert shock.timestamp_ms == 4 * 60_000
    assert shock.previous_timestamp_ms == 0
    assert shock.gap_minutes == 4.0
    assert shock.technical_noise_shock is True
    assert shock.excluded_from_detector is True
    assert shock.excluded_from_ml_dataset is True
    assert shock.reason == "api_maintenance_gap_aftershock"


def test_universe_marks_missing_5m_as_explicit_exclusion_reason() -> None:
    candles_1m = pd.read_csv(FIXTURE_DIR / "candles_1m.csv")
    candles_5m = pd.read_csv(FIXTURE_DIR / "candles_5m.csv")
    rows = build_symbol_universe_by_day(
        candles_1m=candles_1m,
        candles_5m=candles_5m,
        open_interest_5m=None,
        liquidations=None,
    )

    by_symbol = {row.symbol: row for row in rows}
    assert by_symbol["AAA/USDT:USDT"].tradable_on_day is True
    assert by_symbol["BBB/USDT:USDT"].tradable_on_day is False
    assert by_symbol["BBB/USDT:USDT"].reason_if_excluded == "missing_5m_data"


def test_run_mvp1_data_audit_writes_core_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "audit"

    result = run_mvp1_data_audit(input_dir=FIXTURE_DIR, out_dir=out_dir)

    assert result == out_dir
    for name in (
        "anomaly_data_quality.csv",
        "symbol_universe_by_day.csv",
        "anomaly_protocol_audit.csv",
        "anomaly_run_config.csv",
        "artifact_manifest.json",
    ):
        assert (out_dir / name).exists()

    quality_rows = _read_csv_rows(out_dir / "anomaly_data_quality.csv")
    assert not [row for row in quality_rows if row["status"] == "FAIL" and row["severity"] == "critical"]

    universe_rows = _read_csv_rows(out_dir / "symbol_universe_by_day.csv")
    bbb = [row for row in universe_rows if row["symbol"] == "BBB/USDT:USDT"][0]
    assert bbb["tradable_on_day"] == "False"
    assert bbb["reason_if_excluded"] == "missing_5m_data"

    manifest = json.loads((out_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest_names = {entry["name"] for entry in manifest["artifacts"]}
    assert {"anomaly_data_quality.csv", "symbol_universe_by_day.csv", "anomaly_protocol_audit.csv", "anomaly_run_config.csv"} <= manifest_names


def test_run_mvp1_data_audit_cli(tmp_path: Path) -> None:
    out_dir = tmp_path / "cli_audit"

    result = subprocess.run(
        [sys.executable, "main.py", "run-mvp1-data-audit", "--input", str(FIXTURE_DIR), "--out", str(out_dir)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mvp1 data audit artifacts written" in result.stdout
    assert (out_dir / "anomaly_data_quality.csv").exists()
