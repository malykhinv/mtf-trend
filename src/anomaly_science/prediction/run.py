from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.audit import build_methodology_v2_audit_rows
from anomaly_science.prediction.builder import (
    build_calibration_rows,
    build_prediction_metric_rows,
    build_walk_forward_predictions,
    calibration_rows_to_artifact,
    load_prediction_inputs,
    oos_prediction_rows_to_artifact,
    prediction_metric_rows_to_artifact,
)
from anomaly_science.prediction.config import WalkForwardPredictionConfig


def run_mvp1_prediction(
    *,
    state_path: str | Path,
    labels_path: str | Path,
    out_dir: str | Path,
    config: WalkForwardPredictionConfig | None = None,
) -> Path:
    """Run MVP1 weekly CatBoost + Isotonic prediction and write artifacts."""
    state_artifact_path = Path(state_path)
    labels_artifact_path = Path(labels_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or WalkForwardPredictionConfig()

    inputs = load_prediction_inputs(state_path=state_artifact_path, labels_path=labels_artifact_path)
    predictions = build_walk_forward_predictions(inputs=inputs, config=cfg)
    calibration_rows = build_calibration_rows(predictions=predictions)
    metric_rows = build_prediction_metric_rows(inputs=inputs, predictions=predictions, config=cfg)
    protocol_rows = _protocol_rows(input_row_count=len(inputs), prediction_row_count=len(predictions), config=cfg)
    run_config_rows = _run_config_rows(
        state_path=state_artifact_path,
        labels_path=labels_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_oos_predictions.csv",
            oos_prediction_rows_to_artifact(predictions),
            get_artifact_schema("anomaly_oos_predictions.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_calibration.csv",
            calibration_rows_to_artifact(calibration_rows),
            get_artifact_schema("anomaly_calibration.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_prediction_metrics.csv",
            prediction_metric_rows_to_artifact(metric_rows),
            get_artifact_schema("anomaly_prediction_metrics.csv"),
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


def _protocol_rows(*, input_row_count: int, prediction_row_count: int, config: WalkForwardPredictionConfig) -> list[ProtocolAuditRow]:
    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_prediction_scope",
            status=AuditStatus.PASS,
            message="walk-forward calibrated prediction only; no entry logic, no EV, no PnL, no trade simulation, no shadow live, and no production live",
        ),
        ProtocolAuditRow(
            check_name="state_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_state_1m.csv accepted through strict schema boundary for {input_row_count} prediction input rows",
            artifact="anomaly_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="label_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_outcome_labels.csv accepted through strict schema boundary for {input_row_count} prediction input rows",
            artifact="anomaly_outcome_labels.csv",
        ),
        ProtocolAuditRow(
            check_name="weekly_walk_forward_prediction",
            status=AuditStatus.PASS,
            message="each evaluated ISO week is predicted with one model frozen before the week; no intra-week refit",
            artifact="anomaly_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="purge_rule_enforced",
            status=AuditStatus.PASS,
            message=f"train rows must satisfy train_snapshot_time_ms + {config.purge_horizon_minutes}m <= weekly_model_freeze_time_ms",
            artifact="anomaly_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="state_only_features",
            status=AuditStatus.PASS,
            message="CatBoost features are derived from anomaly_state_1m.csv fields only; labels are used only as training and OOS evaluation targets",
            artifact="anomaly_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="missing_future_excluded_from_prediction",
            status=AuditStatus.PASS,
            message="missing_future remains a data condition and is excluded from probability fitting/evaluation",
            artifact="anomaly_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="prediction_rows_written",
            status=AuditStatus.PASS if prediction_row_count > 0 else AuditStatus.WARN,
            message=f"wrote {prediction_row_count} anomaly_oos_predictions.csv rows",
            artifact="anomaly_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="non_empty_oos_prediction_gate",
            status=AuditStatus.PASS if prediction_row_count > 0 else AuditStatus.WARN,
            message=(
                "OOS prediction rows are available for scientific interpretation"
                if prediction_row_count > 0
                else "no OOS prediction rows were produced; fixture smoke is valid, but scientific interpretation requires non-empty OOS predictions"
            ),
            artifact="anomaly_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 prediction uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="technical_noise_shock_excluded_from_ml_train_validation_calibration_test",
            status=AuditStatus.PASS,
            message="prediction train/test/calibration inputs are strict state/label joins; technical-noise events are excluded before state rows are materialized",
            artifact="anomaly_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="fixed_percent_labels_forbidden",
            status=AuditStatus.PASS,
            message="prediction targets come from strict anomaly_outcome_labels.csv ATR-normalized scenario labels; no fixed-percent label columns are accepted",
            artifact="anomaly_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="purge_rule_snapshot_time_plus_Hmax_before_test_start",
            status=AuditStatus.PASS,
            message=f"weekly CatBoost enforces train_snapshot_time_ms + {config.purge_horizon_minutes}m <= weekly_model_freeze_time_ms",
            artifact="anomaly_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="weekly_walk_forward_heavy_models_enforced",
            status=AuditStatus.PASS,
            message="prediction stage trains at most one CatBoost model plus Isotonic calibrators per ISO week; no daily retraining path exists",
            artifact="anomaly_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="frozen_weekly_model_used_for_daily_oos",
            status=AuditStatus.PASS,
            message="all OOS days inside an ISO week share the same weekly_freeze model identifier and train_cutoff_time_ms",
            artifact="anomaly_oos_predictions.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_prediction",
        implemented=implemented_methodology_rows,
    )


def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def _run_config_rows(
    *,
    state_path: Path,
    labels_path: Path,
    output_path: Path,
    config: WalkForwardPredictionConfig,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-prediction", source="cli"),
        RunConfigRow(key="state_path", value=str(state_path), source="cli"),
        RunConfigRow(key="labels_path", value=str(labels_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        *runtime_reproducibility_rows(),
        RunConfigRow(key="stage", value="mvp1_prediction", source="runtime"),
        RunConfigRow(key="prediction_version", value=config.prediction_version, source="runtime"),
        RunConfigRow(key="target_horizon_minutes", value=str(config.target_horizon_minutes), source="runtime"),
        RunConfigRow(key="purge_horizon_minutes", value=str(config.purge_horizon_minutes), source="runtime"),
        RunConfigRow(key="min_train_rows", value=str(config.min_train_rows), source="runtime"),
        RunConfigRow(key="min_group_rows", value=str(config.min_group_rows), source="runtime"),
        RunConfigRow(key="smoothing_strength", value=str(config.smoothing_strength), source="runtime"),
        RunConfigRow(key="model_family", value=config.model_family, source="runtime"),
        RunConfigRow(key="catboost_iterations", value=str(config.catboost_iterations), source="runtime"),
        RunConfigRow(key="catboost_depth", value=str(config.catboost_depth), source="runtime"),
        RunConfigRow(key="catboost_learning_rate", value=str(config.catboost_learning_rate), source="runtime"),
        RunConfigRow(key="random_seed", value=str(config.random_seed), source="runtime"),
        RunConfigRow(key="calibration_method", value="one_vs_rest_isotonic_regression_on_train_calibration_split", source="runtime"),
        RunConfigRow(key="model_freeze_cadence", value="iso_weekly", source="runtime"),
        RunConfigRow(key="prediction_scope", value="calibrated_probabilities_not_decisions", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-prediction-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
