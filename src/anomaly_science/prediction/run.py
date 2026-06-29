from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.prediction.builder import (
    build_calibration_breakdown_rows,
    build_calibration_rows,
    build_prediction_metric_rows,
    build_walk_forward_prediction_result,
    calibration_breakdown_rows_to_artifact,
    calibration_rows_to_artifact,
    feature_importance_rows_to_artifact,
    load_prediction_inputs,
    model_metadata_rows_to_artifact,
    model_training_diagnostic_rows_to_artifact,
    oos_prediction_rows_to_artifact,
    prediction_metric_rows_to_artifact,
)
from anomaly_science.prediction.config import WalkForwardPredictionConfig
from anomaly_science.strategy.metadata import strategy_metadata_run_config_rows


def run_mvp1_prediction(
    *,
    state_path: str | Path,
    labels_path: str | Path,
    feature_matrix_path: str | Path,
    out_dir: str | Path,
    config: WalkForwardPredictionConfig | None = None,
) -> Path:
    """Run MVP1 weekly CatBoost + Isotonic prediction and write artifacts."""
    state_artifact_path = Path(state_path)
    labels_artifact_path = Path(labels_path)
    feature_artifact_path = Path(feature_matrix_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or WalkForwardPredictionConfig()

    inputs = load_prediction_inputs(
        state_path=state_artifact_path,
        labels_path=labels_artifact_path,
        feature_matrix_path=feature_artifact_path,
        anchor_minutes_since_detection=cfg.supervised_anchor_minutes_since_detection,
        anchor_offsets_minutes_since_detection=cfg.supervised_anchor_offsets_minutes_since_detection,
    )
    prediction_result = build_walk_forward_prediction_result(inputs=inputs, config=cfg)
    predictions = prediction_result.predictions
    calibration_rows = build_calibration_rows(predictions=predictions)
    calibration_breakdown_rows = build_calibration_breakdown_rows(inputs=inputs, predictions=predictions)
    metric_rows = build_prediction_metric_rows(inputs=inputs, predictions=predictions, config=cfg)
    protocol_rows = _protocol_rows(
        input_row_count=len(inputs),
        prediction_row_count=len(predictions),
        predictions=predictions,
        model_metadata=prediction_result.model_metadata,
        config=cfg,
    )
    run_config_rows = _run_config_rows(
        state_path=state_artifact_path,
        labels_path=labels_artifact_path,
        feature_matrix_path=feature_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_oos_predictions.csv",
            oos_prediction_rows_to_artifact(predictions),
            get_artifact_schema("strategy_oos_predictions.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_calibration.csv",
            calibration_rows_to_artifact(calibration_rows),
            get_artifact_schema("strategy_calibration.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_calibration_breakdown.csv",
            calibration_breakdown_rows_to_artifact(calibration_breakdown_rows),
            get_artifact_schema("strategy_calibration_breakdown.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_prediction_metrics.csv",
            prediction_metric_rows_to_artifact(metric_rows),
            get_artifact_schema("strategy_prediction_metrics.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "strategy_model_metadata.csv",
            model_metadata_rows_to_artifact(prediction_result.model_metadata),
            get_artifact_schema("strategy_model_metadata.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "strategy_feature_importance.csv",
            feature_importance_rows_to_artifact(prediction_result.feature_importance),
            get_artifact_schema("strategy_feature_importance.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "strategy_model_training_diagnostics.csv",
            model_training_diagnostic_rows_to_artifact(prediction_result.model_training_diagnostics),
            get_artifact_schema("strategy_model_training_diagnostics.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_protocol_audit.csv",
            _protocol_rows_to_artifact(protocol_rows),
            get_artifact_schema("strategy_protocol_audit.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_run_config.csv",
            [asdict(row) for row in run_config_rows],
            get_artifact_schema("strategy_run_config.csv"),
        )
    )
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _protocol_rows(
    *,
    input_row_count: int,
    prediction_row_count: int,
    predictions: object,
    model_metadata: object,
    config: WalkForwardPredictionConfig,
) -> list[ProtocolAuditRow]:
    from anomaly_science.audit import build_horizon_consistency_audit_rows, build_methodology_v2_audit_rows

    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_prediction_scope",
            status=AuditStatus.PASS,
            message="walk-forward calibrated prediction only; no entry logic, no EV, no PnL, no trade simulation, no shadow live, and no production live",
        ),
        ProtocolAuditRow(
            check_name="state_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"strategy_state_1m.csv accepted through strict schema boundary for {input_row_count} prediction input rows",
            artifact="strategy_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="label_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"strategy_outcome_labels.csv accepted through strict schema boundary for {input_row_count} prediction input rows",
            artifact="strategy_outcome_labels.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"strategy_feature_matrix.csv accepted through strict schema boundary for {input_row_count} prediction input rows",
            artifact="strategy_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="weekly_walk_forward_prediction",
            status=AuditStatus.PASS,
            message="each evaluated ISO week is predicted with one model frozen before the week; no intra-week refit",
            artifact="strategy_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="purge_rule_enforced",
            status=AuditStatus.PASS,
            message=f"train rows must satisfy train_snapshot_time_ms + {config.purge_horizon_minutes}m <= weekly_model_freeze_time_ms",
            artifact="strategy_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="asof_model_features",
            status=AuditStatus.PASS,
            message="CatBoost features are derived from strategy_state_1m.csv plus strategy_feature_matrix.csv as-of fields; labels are used only as targets",
            artifact="strategy_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="missing_future_excluded_from_prediction",
            status=AuditStatus.PASS,
            message="missing_future remains a data condition and is excluded from probability fitting/evaluation",
            artifact="strategy_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="prediction_rows_written",
            status=AuditStatus.PASS if prediction_row_count > 0 else AuditStatus.WARN,
            message=f"wrote {prediction_row_count} strategy_oos_predictions.csv rows",
            artifact="strategy_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="non_empty_oos_prediction_gate",
            status=AuditStatus.PASS if prediction_row_count > 0 else AuditStatus.WARN,
            message=(
                "OOS prediction rows are available for scientific interpretation"
                if prediction_row_count > 0
                else "no OOS prediction rows were produced; fixture smoke is valid, but scientific interpretation requires non-empty OOS predictions"
            ),
            artifact="strategy_oos_predictions.csv",
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
            artifact="strategy_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="fixed_percent_labels_forbidden",
            status=AuditStatus.PASS,
            message="prediction targets come from strict strategy_outcome_labels.csv ATR-normalized scenario labels; no fixed-percent label columns are accepted",
            artifact="strategy_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="purge_rule_snapshot_time_plus_Hmax_before_test_start",
            status=AuditStatus.PASS,
            message=f"weekly CatBoost enforces train_snapshot_time_ms + {config.purge_horizon_minutes}m <= weekly_model_freeze_time_ms",
            artifact="strategy_oos_predictions.csv",
        ),
        *build_horizon_consistency_audit_rows(
            stage="mvp1_prediction",
            strategy_name=config.strategy_name,
            target_horizon_minutes=config.target_horizon_minutes,
            target_label_column=config.target_label_column,
            active_strategy_names=config.active_strategy_names,
            active_h_max_minutes=config.active_h_max_minutes,
            purge_horizon_minutes=config.purge_horizon_minutes,
            prediction_rows=predictions,
            model_metadata_rows=model_metadata,
        ),
        ProtocolAuditRow(
            check_name="weekly_walk_forward_heavy_models_enforced",
            status=AuditStatus.PASS,
            message="prediction stage trains at most one CatBoost model plus Isotonic calibrators per ISO week; no daily retraining path exists",
            artifact="strategy_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="frozen_weekly_model_used_for_daily_oos",
            status=AuditStatus.PASS,
            message="all OOS days inside an ISO week share the same weekly_freeze model identifier and train_cutoff_time_ms",
            artifact="strategy_oos_predictions.csv",
        ),
        ProtocolAuditRow(
            check_name="calibration_breakdowns_written",
            status=AuditStatus.PASS if prediction_row_count > 0 else AuditStatus.WARN,
            message="strategy_calibration_breakdown.csv writes calibration reliability slices by session, week, month, symbol, systemic regime, market shock group, alpha decay, and trigger-age bucket",
            artifact="strategy_calibration_breakdown.csv",
        ),
        ProtocolAuditRow(
            check_name="sample_weight_policy_explicit_and_asof_safe",
            status=AuditStatus.PASS,
            message=(
                f"sample_weight_policy={config.sample_weight_policy}; supervised_anchor_policy_id={config.supervised_anchor_policy_id}; "
                "weights use only event_id/symbol grouping known at snapshot time and never future outcomes"
            ),
            artifact="strategy_model_metadata.csv;strategy_model_training_diagnostics.csv",
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
    feature_matrix_path: Path,
    output_path: Path,
    config: WalkForwardPredictionConfig,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-prediction", source="cli"),
        RunConfigRow(key="state_path", value=str(state_path), source="cli"),
        RunConfigRow(key="labels_path", value=str(labels_path), source="cli"),
        RunConfigRow(key="feature_matrix_path", value=str(feature_matrix_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        *runtime_reproducibility_rows(
            data_paths=(state_path, labels_path, feature_matrix_path),
            config=config,
            extra_config={"command": "run-mvp1-prediction", "stage": "mvp1_prediction"},
        ),
        RunConfigRow(key="stage", value="mvp1_prediction", source="runtime"),
        *strategy_metadata_run_config_rows(strategy_name=config.strategy_name),
        RunConfigRow(key="prediction_version", value=config.prediction_version, source="runtime"),
        RunConfigRow(
            key="supervised_anchor_policy_id",
            value=config.supervised_anchor_policy_id,
            source="runtime",
        ),
        RunConfigRow(
            key="supervised_anchor_offsets_minutes_since_detection",
            value=",".join(str(value) for value in config.supervised_anchor_offsets_minutes_since_detection),
            source="runtime",
        ),
        RunConfigRow(
            key="supervised_anchor_minutes_since_detection",
            value=str(config.supervised_anchor_minutes_since_detection),
            source="runtime",
        ),
        RunConfigRow(key="target_horizon_minutes", value=str(config.target_horizon_minutes), source="runtime"),
        RunConfigRow(key="target_label_column", value=config.target_label_column, source="runtime"),
        RunConfigRow(key="active_h_max_minutes", value=str(config.active_h_max_minutes), source="runtime"),
        RunConfigRow(key="purge_horizon_minutes", value=str(config.purge_horizon_minutes), source="runtime"),
        RunConfigRow(key="min_train_rows", value=str(config.min_train_rows), source="runtime"),
        RunConfigRow(key="min_group_rows", value=str(config.min_group_rows), source="runtime"),
        RunConfigRow(key="smoothing_strength", value=str(config.smoothing_strength), source="runtime"),
        RunConfigRow(key="model_family", value=config.model_family, source="runtime"),
        RunConfigRow(key="catboost_iterations", value=str(config.catboost_iterations), source="runtime"),
        RunConfigRow(key="catboost_depth", value=str(config.catboost_depth), source="runtime"),
        RunConfigRow(key="catboost_learning_rate", value=str(config.catboost_learning_rate), source="runtime"),
        RunConfigRow(key="random_seed", value=str(config.random_seed), source="runtime"),
        RunConfigRow(key="sample_weight_policy", value=config.sample_weight_policy, source="runtime"),
        RunConfigRow(key="calibration_method", value="one_vs_rest_isotonic_regression_on_train_calibration_split", source="runtime"),
        RunConfigRow(key="model_freeze_cadence", value="iso_weekly", source="runtime"),
        RunConfigRow(key="prediction_scope", value="calibrated_probabilities_not_decisions", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-prediction-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
