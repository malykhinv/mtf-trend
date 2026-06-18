from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


def test_run_mvp1_strategy_registry_cli_writes_registry_artifact(tmp_path: Path) -> None:
    out_dir = tmp_path / "strategy_registry"

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "run-mvp1-strategy-registry",
            "--out",
            str(out_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mvp1 strategy registry artifacts written" in result.stdout
    assert (out_dir / "strategy_registry.csv").is_file()
    assert (out_dir / "anomaly_protocol_audit.csv").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "strategy_registry.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert rows[0]["strategy_name"] == "broad_anomaly_v1"
    assert rows[0]["strategy_contract_version"] == "base_strategy_v1"
    assert rows[0]["live_trading_strategy"] == "False"

    with (out_dir / "anomaly_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["base_strategy_contract_valid"]["status"] == "PASS"
    assert audit_by_name["protocol_interpretation_gate"]["status"] == "PASS"
