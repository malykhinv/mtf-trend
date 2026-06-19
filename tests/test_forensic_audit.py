from __future__ import annotations

import csv
from pathlib import Path

from anomaly_science.audit import build_independent_forensic_audit_rows
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus


def _write_artifact(path: Path, artifact_name: str, overrides: dict[str, object]) -> None:
    schema = get_artifact_schema(artifact_name)
    row = {column: "" for column in schema.required_columns}
    row.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(schema.required_columns))
        writer.writeheader()
        writer.writerow(row)


def _copy_text(source: Path, target: Path) -> None:
    target.write_text(source.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")


def _write_valid_minimal_forensic_fixture(root: Path) -> None:
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


def _by_name(rows):
    return {row.check_name: row for row in rows}


def test_independent_forensic_audit_passes_valid_minimal_artifacts(tmp_path: Path) -> None:
    _write_valid_minimal_forensic_fixture(tmp_path)

    rows = build_independent_forensic_audit_rows(tmp_path)
    by_name = _by_name(rows)

    assert by_name["forensic_artifact_schema_columns_verified"].status is AuditStatus.PASS
    assert by_name["forensic_temporal_contract_verified_from_artifacts"].status is AuditStatus.PASS
    assert by_name["forensic_model_metadata_purge_hmax_verified"].status is AuditStatus.PASS
    assert by_name["forensic_prediction_rows_are_oos_after_train_cutoff"].status is AuditStatus.PASS
    assert by_name["forensic_prediction_model_horizon_identity_consistent"].status is AuditStatus.PASS
    assert by_name["forensic_canonical_alias_artifacts_match"].status is AuditStatus.PASS
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
