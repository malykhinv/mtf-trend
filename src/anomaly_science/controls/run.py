from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.audit import build_methodology_v2_audit_rows
from anomaly_science.controls.builder import (
    baseline_comparison_rows_to_artifact,
    build_baseline_comparison_rows,
    build_placebo_test_rows,
    placebo_test_rows_to_artifact,
)
from anomaly_science.controls.config import ControlsConfig
from anomaly_science.prediction import load_prediction_inputs


def run_mvp1_controls(
    *,
    state_path: str | Path,
    labels_path: str | Path,
    out_dir: str | Path,
    config: ControlsConfig | None = None,
) -> Path:
    """Run MVP1 placebo/control checks and write control artifacts."""
    state_artifact_path = Path(state_path)
    labels_artifact_path = Path(labels_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or ControlsConfig()

    inputs = load_prediction_inputs(state_path=state_artifact_path, labels_path=labels_artifact_path)
    placebo_rows = build_placebo_test_rows(inputs=inputs, config=cfg)
    baseline_rows = build_baseline_comparison_rows(inputs=inputs, config=cfg)
    ok_control_count = sum(1 for row in (*placebo_rows, *baseline_rows) if row.status == "OK")
    protocol_rows = _protocol_rows(
        input_row_count=len(inputs),
        placebo_count=len(placebo_rows),
        baseline_count=len(baseline_rows),
        ok_control_count=ok_control_count,
        config=cfg,
    )
    run_config_rows = _run_config_rows(
        state_path=state_artifact_path,
        labels_path=labels_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.append(
        write_csv_artifact(
            output_path / "anomaly_placebo_tests.csv",
            placebo_test_rows_to_artifact(placebo_rows),
            get_artifact_schema("anomaly_placebo_tests.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_baseline_comparison.csv",
            baseline_comparison_rows_to_artifact(baseline_rows),
            get_artifact_schema("anomaly_baseline_comparison.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_protocol_audit.csv",
            _protocol_rows_to_artifact(protocol_rows),
            get_artifact_schema("anomaly_protocol_audit.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_run_config.csv",
            [asdict(row) for row in run_config_rows],
            get_artifact_schema("anomaly_run_config.csv"),
        )
    )
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _protocol_rows(
    *,
    input_row_count: int,
    placebo_count: int,
    baseline_count: int,
    ok_control_count: int,
    config: ControlsConfig,
) -> list[ProtocolAuditRow]:
    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_controls_scope",
            status=AuditStatus.PASS,
            message="placebo/control checks only; no entry logic, no EV, no PnL, no trade simulation, no shadow live, and no production live",
        ),
        ProtocolAuditRow(
            check_name="state_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_state_1m.csv accepted through strict schema boundary for {input_row_count} control input rows",
            artifact="anomaly_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="label_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_outcome_labels.csv accepted through strict schema boundary for {input_row_count} control input rows",
            artifact="anomaly_outcome_labels.csv",
        ),
        ProtocolAuditRow(
            check_name="walk_forward_boundary_reused",
            status=AuditStatus.PASS,
            message=f"control evaluations reuse daily prequential purge rule with horizon {config.purge_horizon_minutes}m",
            artifact="anomaly_placebo_tests.csv",
        ),
        ProtocolAuditRow(
            check_name="placebo_labels_are_controls_only",
            status=AuditStatus.PASS,
            message="shuffled labels are generated only inside controls and are not written as scientific outcome labels",
            artifact="anomaly_placebo_tests.csv",
        ),
        ProtocolAuditRow(
            check_name="no_proxy_volume_baseline",
            status=AuditStatus.PASS,
            message="volume_only baseline is deferred when volume columns are absent from anomaly_state_1m.csv; no proxy volume fallback is used",
            artifact="anomaly_baseline_comparison.csv",
        ),
        ProtocolAuditRow(
            check_name="control_rows_written",
            status=AuditStatus.PASS if ok_control_count > 0 else AuditStatus.WARN,
            message=f"wrote {placebo_count} placebo rows and {baseline_count} baseline comparison rows",
            artifact="anomaly_placebo_tests.csv;anomaly_baseline_comparison.csv",
        ),
        ProtocolAuditRow(
            check_name="non_empty_oos_control_gate",
            status=AuditStatus.PASS if ok_control_count > 0 else AuditStatus.WARN,
            message=(
                f"{ok_control_count} control/baseline rows produced OOS evaluations"
                if ok_control_count > 0
                else "no control/baseline row produced OOS evaluations; fixture smoke is valid, but scientific interpretation requires non-empty controls"
            ),
            artifact="anomaly_placebo_tests.csv;anomaly_baseline_comparison.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 controls use anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="technical_noise_shock_excluded_from_ml_train_validation_calibration_test",
            status=AuditStatus.PASS,
            message="controls reuse strict state/label prediction inputs; technical-noise events are excluded before state rows are materialized",
            artifact="anomaly_placebo_tests.csv;anomaly_baseline_comparison.csv",
        ),
        ProtocolAuditRow(
            check_name="purge_rule_snapshot_time_plus_Hmax_before_test_start",
            status=AuditStatus.PASS,
            message=f"controls reuse daily prequential purge horizon {config.purge_horizon_minutes}m before each test day",
            artifact="anomaly_placebo_tests.csv;anomaly_baseline_comparison.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_controls",
        implemented=implemented_methodology_rows,
    )


def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def _run_config_rows(*, state_path: Path, labels_path: Path, output_path: Path, config: ControlsConfig) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-controls", source="cli"),
        RunConfigRow(key="state_path", value=str(state_path), source="cli"),
        RunConfigRow(key="labels_path", value=str(labels_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        *runtime_reproducibility_rows(),
        RunConfigRow(key="stage", value="mvp1_controls", source="runtime"),
        RunConfigRow(key="control_version", value=config.control_version, source="runtime"),
        RunConfigRow(key="target_horizon_minutes", value=str(config.target_horizon_minutes), source="runtime"),
        RunConfigRow(key="purge_horizon_minutes", value=str(config.purge_horizon_minutes), source="runtime"),
        RunConfigRow(key="min_train_rows", value=str(config.min_train_rows), source="runtime"),
        RunConfigRow(key="min_group_rows", value=str(config.min_group_rows), source="runtime"),
        RunConfigRow(key="smoothing_strength", value=str(config.smoothing_strength), source="runtime"),
        RunConfigRow(key="random_seed", value=str(config.random_seed), source="runtime"),
        RunConfigRow(key="control_scope", value="negative_placebo_and_baseline_checks_not_decisions", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-controls-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
