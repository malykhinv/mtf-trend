from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


def test_run_mvp1_holdout_governance_writes_freeze_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "governance"

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-holdout-governance",
            "--out",
            str(out_dir),
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2024-12-15",
            "--freeze-id",
            "freeze-test",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mvp1 holdout governance artifacts written" in result.stdout
    assert (out_dir / "research_ledger.csv").is_file()
    assert (out_dir / "holdout_access_log.csv").is_file()
    assert (out_dir / "strategy_protocol_audit.csv").is_file()
    assert (out_dir / "strategy_run_config.csv").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "research_ledger.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        ledger = list(csv.DictReader(file_obj))
    assert ledger[0]["protocol_freeze_id"] == "freeze-test"
    assert ledger[0]["final_holdout_start_date"] == "2024-10-17"

    with (out_dir / "holdout_access_log.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        access_rows = list(csv.DictReader(file_obj))
    assert access_rows == []

    with (out_dir / "strategy_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["holdout_access_log_matches_research_mode"]["status"] == "PASS"
    assert audit_by_name["final_holdout_not_accessed_before_protocol_freeze"]["status"] == "PASS"
    assert audit_by_name["protocol_interpretation_gate"]["status"] == "PASS"


def test_run_mvp1_holdout_governance_logs_explicit_frozen_holdout_access(tmp_path: Path) -> None:
    out_dir = tmp_path / "governance_frozen"

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-holdout-governance",
            "--out",
            str(out_dir),
            "--start-date",
            "2024-01-01",
            "--end-date",
            "2024-12-15",
            "--freeze-id",
            "freeze-test",
            "--research-mode",
            "frozen_holdout",
            "--holdout-access-artifact",
            "manual frozen holdout test",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    with (out_dir / "holdout_access_log.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        access_rows = list(csv.DictReader(file_obj))
    assert len(access_rows) == 1
    assert access_rows[0]["protocol_freeze_id"] == "freeze-test"
    assert access_rows[0]["artifact"] == "manual frozen holdout test"
    assert access_rows[0]["access_approved"] == "True"

    with (out_dir / "strategy_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["holdout_access_log_matches_research_mode"]["status"] == "PASS"
