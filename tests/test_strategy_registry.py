from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

from anomaly_science.strategy import anomaly_reject_reason_codes, specified_not_implemented_strategy_names


EXPECTED_ANOMALY_REJECT_REASONS = {
    "not_triggered",
    "technical_noise_shock",
    "warmup_after_data_gap",
    "cascade_suppressed",
    "data_quality_fail",
    "insufficient_history_for_ATR",
    "insufficient_cross_section",
    "missing_required_liquidation_data",
    "missing_required_oi_data",
    "horizon_not_available",
    "future_path_incomplete",
    "anti_binary_rule_failed",
    "causality_gate_failed",
    "outside_strategy_lifecycle",
    "RR_unacceptable",
    "calibrated_confidence_too_low",
    "systemic_cluster_guardrail",
}


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
    assert (out_dir / "strategy_implementation_status.csv").is_file()
    assert (out_dir / "strategy_reject_reasons.csv").is_file()
    assert (out_dir / "strategy_protocol_audit.csv").is_file()
    assert (out_dir / "artifact_manifest.json").is_file()

    with (out_dir / "strategy_registry.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert [row["strategy_name"] for row in rows] == [
        "broad_anomaly_v1_h15",
        "broad_anomaly_v1_h30",
        "broad_anomaly_v1_h60",
    ]
    assert rows[0]["strategy_contract_version"] == "base_strategy_v1"
    assert rows[0]["allowed_horizons"] == "15;30;60"
    assert rows[0]["default_horizon_minutes"] == "30"
    assert {row["horizon_minutes"] for row in rows} == {"15", "30", "60"}
    assert {row["allowed_horizons"] for row in rows} == {"15;30;60"}
    assert {row["default_horizon_minutes"] for row in rows} == {"30"}
    assert rows[0]["required_data_streams"] == "liquidations=false;open_interest=false"
    assert rows[0]["live_trading_strategy"] == "False"

    with (out_dir / "strategy_implementation_status.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        status_rows = list(csv.DictReader(file_obj))
    status_by_name = {row["strategy_name"]: row for row in status_rows}
    assert [row["strategy_name"] for row in status_rows] == [
        "broad_anomaly_v1_h15",
        "broad_anomaly_v1_h30",
        "broad_anomaly_v1_h60",
        "post_anomaly_extension_v1_h60",
        "post_anomaly_extension_v1_h120",
        "post_anomaly_extension_v1_h180",
        "post_pump_distribution_v1_h60",
        "post_pump_distribution_v1_h120",
        "post_pump_distribution_v1_h180",
    ]
    assert {status_by_name[name]["implementation_status"] for name in [
        "broad_anomaly_v1_h15",
        "broad_anomaly_v1_h30",
        "broad_anomaly_v1_h60",
    ]} == {"implemented"}
    assert {status_by_name[name]["implementation_status"] for name in specified_not_implemented_strategy_names()} == {
        "specified_not_implemented"
    }
    assert status_by_name["broad_anomaly_v1_h30"]["executable"] == "True"
    assert status_by_name["post_anomaly_extension_v1_h180"]["executable"] == "False"
    assert status_by_name["post_anomaly_extension_v1_h180"]["registry_error"] == (
        "strategy variant is specified but not implemented yet"
    )

    with (out_dir / "strategy_reject_reasons.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        reject_rows = list(csv.DictReader(file_obj))
    assert {row["reason_code"] for row in reject_rows} == EXPECTED_ANOMALY_REJECT_REASONS
    assert {row["strategy_name"] for row in reject_rows} == {
        "broad_anomaly_v1_h15",
        "broad_anomaly_v1_h30",
        "broad_anomaly_v1_h60",
    }

    with (out_dir / "strategy_protocol_audit.csv").open(encoding="utf-8-sig", newline="") as file_obj:
        audit_by_name = {row["check_name"]: row for row in csv.DictReader(file_obj)}
    assert audit_by_name["base_strategy_contract_valid"]["status"] == "PASS"
    assert audit_by_name["strategy_reject_reasons_declared"]["status"] == "PASS"
    assert audit_by_name["strategy_implementation_status_truthful"]["status"] == "PASS"
    assert audit_by_name["protocol_interpretation_gate"]["status"] == "PASS"


def test_anomaly_reject_reason_contract_matches_strategy_doc_minimum() -> None:
    assert anomaly_reject_reason_codes() == EXPECTED_ANOMALY_REJECT_REASONS
