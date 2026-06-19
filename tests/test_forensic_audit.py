from __future__ import annotations

import csv
import json
from pathlib import Path

from anomaly_science.audit import build_independent_forensic_audit_rows
from anomaly_science.research.run import _write_forensic_audit
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus
from anomaly_science.contracts.decision import EXPECTED_VALUE_TEMPORAL_CONTRACT
from anomaly_science.contracts.execution import EV_ENTRY_PRICE_BASIS, EV_EXECUTION_REFERENCE_MODEL, ROUND_TRIP_COST_MODEL, SIMULATION_ENTRY_PRICE_BASIS
from anomaly_science.contracts.simulation import TRADE_SIMULATION_TEMPORAL_CONTRACT


def _write_artifact(path: Path, artifact_name: str, overrides: dict[str, object]) -> None:
    _write_artifact_rows(path, artifact_name, [overrides])


def _write_artifact_rows(path: Path, artifact_name: str, rows: list[dict[str, object]]) -> None:
    schema = get_artifact_schema(artifact_name)
    payload = []
    for row in rows:
        item = {column: "" for column in schema.required_columns}
        item.update(row)
        payload.append(item)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(schema.required_columns))
        writer.writeheader()
        writer.writerows(payload)


def _copy_text(source: Path, target: Path) -> None:
    target.write_text(source.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")


def _read_csv_payload(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return list(csv.DictReader(file_obj))


def _write_root_research_manifest(root: Path) -> None:
    rows = [
        {"key": "strategy_name", "value": "broad_anomaly_v1_h30", "source": "run_research"},
        {"key": "strategy_version", "value": "v1", "source": "strategy_registry"},
        {"key": "strategy_contract_version", "value": "base_strategy_v1", "source": "strategy_registry"},
        {"key": "strategy_family", "value": "anomaly", "source": "strategy_registry"},
        {"key": "target_horizon_minutes", "value": "30", "source": "run_research"},
        {"key": "active_h_max_minutes", "value": "30", "source": "run_research"},
        {"key": "research_mode", "value": "frozen_holdout", "source": "run_research"},
        {"key": "holdout_days", "value": "60", "source": "run_research"},
        {"key": "protocol_freeze_id", "value": "freeze", "source": "run_research"},
        {"key": "research_start_date", "value": "2026-01-01", "source": "run_research"},
        {"key": "research_end_date", "value": "2026-01-02", "source": "run_research"},
        {"key": "forensic_audit_status", "value": "PENDING", "source": "run_research"},
        {"key": "data_snapshot_hash", "value": "abc", "source": "runtime"},
        {"key": "config_hash", "value": "def", "source": "runtime"},
        {"key": "dependency_versions", "value": "pandas==fixture", "source": "runtime"},
        {"key": "artifact_manifest_path", "value": str(root / "artifact_manifest.json"), "source": "run_research"},
        {"key": "methodology_gap_ledger_status", "value": "MISSING=0;PARTIAL=0", "source": "research_ledger"},
    ]
    schema = get_artifact_schema("strategy_run_config.csv")
    with (root / "strategy_run_config.csv").open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(schema.required_columns))
        writer.writeheader()
        writer.writerows(rows)
    _copy_text(root / "strategy_run_config.csv", root / "anomaly_run_config.csv")
    (root / "artifact_manifest.json").write_text(
        json.dumps({"run_id": "fixture", "created_at_utc": "2026-01-01T00:00:00+00:00", "artifacts": []}) + "\n",
        encoding="utf-8",
    )


def _write_valid_minimal_forensic_fixture(root: Path) -> None:
    _write_root_research_manifest(root)
    prediction = root / "stages" / "prediction"
    _write_artifact(
        prediction / "strategy_oos_predictions.csv",
        "strategy_oos_predictions.csv",
        {
            "prediction_version": "wf_v1",
            "strategy_name": "broad_anomaly_v1_h30",
            "strategy_version": "v1",
            "strategy_contract_version": "base_strategy_v1",
            "target_label_column": "scenario_30m",
            "active_h_max_minutes": 30,
            "event_id": "e1",
            "symbol": "AAAUSDT",
            "snapshot_time_ms": 1_800_000,
            "feature_cutoff_time_ms": 1_800_000,
            "future_start_time_ms": 1_860_000,
            "target_horizon_minutes": 30,
            "target_scenario": "long_continuation",
            "test_day": "2026-01-02",
            "train_cutoff_time_ms": 600_000,
            "model_family": "catboost",
            "model_key": "m1",
            "model_train_row_count": 100,
            "model_group_row_count": 100,
            "raw_p_long_continuation": 0.7,
            "raw_p_short_fade": 0.1,
            "raw_p_static_or_chop": 0.1,
            "raw_p_unclear": 0.1,
            "p_long_continuation": 0.7,
            "p_short_fade": 0.1,
            "p_static_or_chop": 0.1,
            "p_unclear": 0.1,
            "predicted_scenario": "long_continuation",
            "prediction_confidence": 0.7,
            "temporal_contract": "features<=snapshot<labels",
        },
    )
    _copy_text(prediction / "strategy_oos_predictions.csv", prediction / "anomaly_oos_predictions.csv")
    _write_artifact(
        prediction / "strategy_model_metadata.csv",
        "strategy_model_metadata.csv",
        {
            "prediction_version": "wf_v1",
            "model_key": "m1",
            "model_family": "catboost",
            "strategy_name": "broad_anomaly_v1_h30",
            "strategy_version": "v1",
            "strategy_contract_version": "base_strategy_v1",
            "target_horizon_minutes": 30,
            "target_label_column": "scenario_30m",
            "active_h_max_minutes": 30,
            "weekly_model_freeze_time_ms": 2_400_000,
            "train_cutoff_time_ms": 600_000,
            "train_row_count": 100,
            "fit_row_count": 60,
            "validation_row_count": 20,
            "calibration_row_count": 20,
            "best_iteration": 12,
            "class_order": "long_continuation|short_fade|static_or_chop|unclear",
            "model_feature_names": "feature_a|feature_b",
            "calibration_method": "isotonic",
        },
    )
    _write_artifact(
        prediction / "strategy_protocol_audit.csv",
        "strategy_protocol_audit.csv",
        {
            "check_name": "some_stage_check",
            "status": "PASS",
            "message": "stage check passed",
            "artifact": "strategy_oos_predictions.csv",
        },
    )
    _copy_text(prediction / "strategy_protocol_audit.csv", prediction / "anomaly_protocol_audit.csv")
    decision = root / "stages" / "decision"
    _write_artifact(
        decision / "strategy_decision_timing.csv",
        "strategy_decision_timing.csv",
        {
            "ev_version": "mvp1_expected_value_oos_calibrated_proxy_v1",
            "strategy_name": "broad_anomaly_v1_h30",
            "strategy_version": "v1",
            "event_id": "e1",
            "symbol": "AAAUSDT",
            "state_time_ms": 1_800_000,
            "snapshot_time_ms": 1_800_000,
            "feature_cutoff_time_ms": 1_800_000,
            "future_start_time_ms": 1_860_000,
            "target_horizon_minutes": 30,
            "execution_reference_model": EV_EXECUTION_REFERENCE_MODEL,
            "entry_price_basis": EV_ENTRY_PRICE_BASIS,
            "entry_reference_price": 100.0,
            "ATR_1d_asof_t": 2.0,
            "stop_distance": 2.0,
            "target_distance": 3.0,
            "fee_bps": 4.0,
            "slippage_bps": 2.0,
            "cost_model": ROUND_TRIP_COST_MODEL,
            "cost_penalty": 0.0812077584,
            "p_follow_through_long": 0.8,
            "p_adverse_long": 0.1,
            "p_follow_through_short": 0.1,
            "p_adverse_short": 0.8,
            "confidence_calibrated": 0.8,
            "RR_long_proxy": 1.5,
            "RR_short_proxy": 1.5,
            "EV_long": 2.0,
            "EV_short": -1.0,
            "EV_wait": 0.0,
            "EV_no_trade": 0.0,
            "best_action": "long",
            "is_prediction_confident": "True",
            "is_RR_still_acceptable": "True",
            "temporal_contract": EXPECTED_VALUE_TEMPORAL_CONTRACT,
        },
    )
    _copy_text(decision / "strategy_decision_timing.csv", decision / "anomaly_decision_timing.csv")
    simulation = root / "stages" / "simulation"
    _write_artifact(
        simulation / "strategy_trade_simulation.csv",
        "strategy_trade_simulation.csv",
        {
            "simulation_version": "mvp1_trade_simulation_pessimistic_v1",
            "strategy_name": "broad_anomaly_v1_h30",
            "strategy_version": "v1",
            "event_id": "e1",
            "symbol": "AAAUSDT",
            "snapshot_time_ms": 1_800_000,
            "feature_cutoff_time_ms": 1_800_000,
            "target_horizon_minutes": 30,
            "execution_reference_model": EV_EXECUTION_REFERENCE_MODEL,
            "entry_price_basis": SIMULATION_ENTRY_PRICE_BASIS,
            "decision_action": "long",
            "simulated_side": "long",
            "entry_reference_time_ms": 1_860_000,
            "entry_reference_open": 100.0,
            "entry_price": 100.02,
            "ATR_1d_asof_t": 2.0,
            "stop_distance": 2.0,
            "target_distance": 3.0,
            "stop_price": 98.02,
            "target_price": 103.02,
            "fee_bps": 4.0,
            "slippage_bps": 2.0,
            "cost_model": ROUND_TRIP_COST_MODEL,
            "total_cost": 0.0812077584,
            "funding_cost": 0.0,
            "exit_time_ms": 1_920_000,
            "exit_price": 102.999396,
            "exit_reason": "target_hit",
            "gross_pnl": 2.979396,
            "net_pnl": 2.8981882416,
            "net_pnl_r": 1.4490941208,
            "net_return": 0.0289760852,
            "barrier_resolution": "single_barrier",
            "temporal_contract": TRADE_SIMULATION_TEMPORAL_CONTRACT,
        },
    )
    _copy_text(simulation / "strategy_trade_simulation.csv", simulation / "anomaly_trade_simulation.csv")
    _write_artifact_rows(
        simulation / "strategy_trade_simulation_metrics.csv",
        "strategy_trade_simulation_metrics.csv",
        [
            {
                "simulation_version": "mvp1_trade_simulation_pessimistic_v1",
                "target_horizon_minutes": 30,
                "metric_name": metric_name,
                "metric_value": "1",
                "row_count": 1,
                "notes": "fixture",
            }
            for metric_name in (
                "always_no_trade_baseline_net_pnl",
                "random_entry_time_control_rows",
                "random_entry_time_control_total_net_pnl",
                "delta_vs_always_no_trade_net_pnl",
                "delta_vs_random_entry_time_net_pnl",
            )
        ],
    )
    _copy_text(simulation / "strategy_trade_simulation_metrics.csv", simulation / "anomaly_trade_simulation_metrics.csv")
    controls = root / "stages" / "controls"
    _write_artifact_rows(
        controls / "strategy_placebo_tests.csv",
        "strategy_placebo_tests.csv",
        [
            {
                "control_version": "mvp1_controls_v1",
                "control_name": control_name,
                "target_horizon_minutes": 30,
                "random_seed": 7,
                "available_label_rows": 20,
                "oos_prediction_rows": 4,
                "accuracy": 0.25,
                "multiclass_brier": 0.75,
                "log_loss": 1.0,
                "reference_real_brier": 0.6,
                "brier_delta_vs_real": 0.15,
                "status": "OK",
                "notes": "fixture",
            }
            for control_name in ("random_labels", "time_shuffled_labels", "symbol_shuffled_labels")
        ],
    )
    _copy_text(controls / "strategy_placebo_tests.csv", controls / "anomaly_placebo_tests.csv")
    _write_artifact_rows(
        controls / "strategy_baseline_comparison.csv",
        "strategy_baseline_comparison.csv",
        [
            {
                "control_version": "mvp1_controls_v1",
                "baseline_name": baseline_name,
                "feature_family": "fixture",
                "target_horizon_minutes": 30,
                "available_label_rows": 20,
                "oos_prediction_rows": 4,
                "accuracy": 0.25,
                "multiclass_brier": 0.75,
                "log_loss": 1.0,
                "reference_real_brier": 0.6,
                "brier_delta_vs_real": 0.15,
                "status": "OK",
                "notes": "fixture",
            }
            for baseline_name in (
                "global_prior_only",
                "session_only",
                "event_time_only",
                "price_path_only",
                "volume_only",
                "btc_eth_only",
                "always_follow_anomaly",
                "always_fade_anomaly",
                "fade_only_after_extension",
                "follow_only_early_squeeze",
                "no_cvd_features_ablation",
                "no_oi_features_ablation",
                "no_liquidation_features_ablation",
                "idiosyncratic_only_subset",
                "systemic_cluster_only_subset",
            )
        ],
    )
    _copy_text(controls / "strategy_baseline_comparison.csv", controls / "anomaly_baseline_comparison.csv")


def _by_name(rows):
    return {row.check_name: row for row in rows}


def test_independent_forensic_audit_passes_valid_minimal_artifacts(tmp_path: Path) -> None:
    _write_valid_minimal_forensic_fixture(tmp_path)

    rows = build_independent_forensic_audit_rows(tmp_path)
    by_name = _by_name(rows)

    assert by_name["forensic_artifact_schema_columns_verified"].status is AuditStatus.PASS
    assert by_name["forensic_root_research_run_manifest_complete"].status is AuditStatus.PASS
    assert by_name["forensic_temporal_contract_verified_from_artifacts"].status is AuditStatus.PASS
    assert by_name["forensic_model_metadata_purge_hmax_verified"].status is AuditStatus.PASS
    assert by_name["forensic_prediction_rows_are_oos_after_train_cutoff"].status is AuditStatus.PASS
    assert by_name["forensic_prediction_model_horizon_identity_consistent"].status is AuditStatus.PASS
    assert by_name["forensic_canonical_alias_artifacts_match"].status is AuditStatus.PASS
    assert by_name["forensic_simulation_decision_contract_alignment_verified"].status is AuditStatus.PASS
    assert by_name["forensic_simulation_pessimistic_prices_and_costs_verified"].status is AuditStatus.PASS
    assert by_name["forensic_simulation_no_parallel_symbol_positions_verified"].status is AuditStatus.PASS
    assert by_name["forensic_required_controls_complete"].status is AuditStatus.PASS
    assert by_name["forensic_stage_protocol_audit_has_no_fail_rows"].status is AuditStatus.PASS
    assert by_name["forensic_protocol_interpretation_gate"].status is AuditStatus.PASS


def test_independent_forensic_audit_fails_future_leak(tmp_path: Path) -> None:
    _write_valid_minimal_forensic_fixture(tmp_path)
    path = tmp_path / "stages" / "prediction" / "strategy_oos_predictions.csv"
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace(",1800000,1800000,1860000,", ",1800000,1800001,1860000,")
    path.write_text(text, encoding="utf-8-sig")
    _copy_text(path, tmp_path / "stages" / "prediction" / "anomaly_oos_predictions.csv")

    rows = build_independent_forensic_audit_rows(tmp_path)
    by_name = _by_name(rows)

    assert by_name["forensic_temporal_contract_verified_from_artifacts"].status is AuditStatus.FAIL
    assert by_name["forensic_protocol_interpretation_gate"].status is AuditStatus.FAIL


def test_independent_forensic_audit_fails_purge_violation(tmp_path: Path) -> None:
    _write_valid_minimal_forensic_fixture(tmp_path)
    path = tmp_path / "stages" / "prediction" / "strategy_model_metadata.csv"
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace(",2400000,600000,", ",2000000,600000,")
    path.write_text(text, encoding="utf-8-sig")

    rows = build_independent_forensic_audit_rows(tmp_path)
    by_name = _by_name(rows)

    assert by_name["forensic_model_metadata_purge_hmax_verified"].status is AuditStatus.FAIL
    assert by_name["forensic_protocol_interpretation_gate"].status is AuditStatus.FAIL


def test_independent_forensic_audit_fails_alias_drift(tmp_path: Path) -> None:
    _write_valid_minimal_forensic_fixture(tmp_path)
    alias_path = tmp_path / "stages" / "prediction" / "anomaly_oos_predictions.csv"
    alias_path.write_text(alias_path.read_text(encoding="utf-8-sig").replace("0.7", "0.6", 1), encoding="utf-8-sig")

    rows = build_independent_forensic_audit_rows(tmp_path)
    by_name = _by_name(rows)

    assert by_name["forensic_canonical_alias_artifacts_match"].status is AuditStatus.FAIL
    assert by_name["forensic_protocol_interpretation_gate"].status is AuditStatus.FAIL


def test_independent_forensic_audit_fails_non_pessimistic_simulation_entry(tmp_path: Path) -> None:
    _write_valid_minimal_forensic_fixture(tmp_path)
    path = tmp_path / "stages" / "simulation" / "strategy_trade_simulation.csv"
    rows = _read_csv_payload(path)
    rows[0]["entry_price"] = "99.99"
    rows[0]["stop_price"] = "97.99"
    rows[0]["target_price"] = "102.99"
    _write_artifact_rows(path, "strategy_trade_simulation.csv", [dict(rows[0])])
    _copy_text(path, tmp_path / "stages" / "simulation" / "anomaly_trade_simulation.csv")

    result = build_independent_forensic_audit_rows(tmp_path)
    by_name = _by_name(result)

    assert by_name["forensic_simulation_pessimistic_prices_and_costs_verified"].status is AuditStatus.FAIL
    assert by_name["forensic_protocol_interpretation_gate"].status is AuditStatus.FAIL


def test_independent_forensic_audit_fails_parallel_simulation_positions(tmp_path: Path) -> None:
    _write_valid_minimal_forensic_fixture(tmp_path)
    decision_path = tmp_path / "stages" / "decision" / "strategy_decision_timing.csv"
    decision_rows = _read_csv_payload(decision_path)
    second_decision = dict(decision_rows[0])
    second_decision["event_id"] = "e2"
    second_decision["snapshot_time_ms"] = "1810000"
    second_decision["feature_cutoff_time_ms"] = "1810000"
    second_decision["future_start_time_ms"] = "1870000"
    _write_artifact_rows(decision_path, "strategy_decision_timing.csv", [dict(decision_rows[0]), second_decision])
    _copy_text(decision_path, tmp_path / "stages" / "decision" / "anomaly_decision_timing.csv")

    simulation_path = tmp_path / "stages" / "simulation" / "strategy_trade_simulation.csv"
    simulation_rows = _read_csv_payload(simulation_path)
    second_simulation = dict(simulation_rows[0])
    second_simulation["event_id"] = "e2"
    second_simulation["snapshot_time_ms"] = "1810000"
    second_simulation["feature_cutoff_time_ms"] = "1810000"
    second_simulation["entry_reference_time_ms"] = "1870000"
    second_simulation["exit_time_ms"] = "1930000"
    _write_artifact_rows(simulation_path, "strategy_trade_simulation.csv", [dict(simulation_rows[0]), second_simulation])
    _copy_text(simulation_path, tmp_path / "stages" / "simulation" / "anomaly_trade_simulation.csv")

    result = build_independent_forensic_audit_rows(tmp_path)
    by_name = _by_name(result)

    assert by_name["forensic_simulation_no_parallel_symbol_positions_verified"].status is AuditStatus.FAIL
    assert by_name["forensic_protocol_interpretation_gate"].status is AuditStatus.FAIL


def test_independent_forensic_audit_fails_missing_required_control(tmp_path: Path) -> None:
    _write_valid_minimal_forensic_fixture(tmp_path)
    path = tmp_path / "stages" / "controls" / "strategy_baseline_comparison.csv"
    rows = [row for row in _read_csv_payload(path) if row["baseline_name"] != "btc_eth_only"]
    _write_artifact_rows(path, "strategy_baseline_comparison.csv", [dict(row) for row in rows])
    _copy_text(path, tmp_path / "stages" / "controls" / "anomaly_baseline_comparison.csv")

    result = build_independent_forensic_audit_rows(tmp_path)
    by_name = _by_name(result)

    assert by_name["forensic_required_controls_complete"].status is AuditStatus.FAIL
    assert by_name["forensic_protocol_interpretation_gate"].status is AuditStatus.FAIL



def test_independent_forensic_audit_fails_missing_root_manifest(tmp_path: Path) -> None:
    _write_valid_minimal_forensic_fixture(tmp_path)
    (tmp_path / "strategy_run_config.csv").unlink()
    (tmp_path / "anomaly_run_config.csv").unlink()

    rows = build_independent_forensic_audit_rows(tmp_path)
    by_name = _by_name(rows)

    assert by_name["forensic_root_research_run_manifest_complete"].status is AuditStatus.FAIL
    assert by_name["forensic_protocol_interpretation_gate"].status is AuditStatus.FAIL


def test_run_research_forensic_writer_persists_pass_rows(tmp_path: Path) -> None:
    _write_valid_minimal_forensic_fixture(tmp_path)

    audit_dir, status, fail_count, warn_count, failed_checks = _write_forensic_audit(tmp_path)

    assert audit_dir == tmp_path / "stages" / "forensic_audit"
    assert (audit_dir / "strategy_protocol_audit.csv").is_file()
    assert status == "PASS"
    assert fail_count == 0
    assert warn_count == 0
    assert failed_checks == ""


def test_run_research_forensic_writer_reports_fail_rows(tmp_path: Path) -> None:
    _write_valid_minimal_forensic_fixture(tmp_path)
    path = tmp_path / "stages" / "prediction" / "strategy_model_metadata.csv"
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace(",2400000,600000,", ",2000000,600000,")
    path.write_text(text, encoding="utf-8-sig")

    audit_dir, status, fail_count, warn_count, failed_checks = _write_forensic_audit(tmp_path)

    assert (audit_dir / "strategy_protocol_audit.csv").is_file()
    assert status == "FAIL"
    assert fail_count >= 1
    assert "forensic_model_metadata_purge_hmax_verified" in failed_checks
