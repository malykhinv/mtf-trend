from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.audit import build_methodology_v2_audit_rows
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.decision.builder import (
    build_expected_value_metric_rows,
    expected_value_metric_rows_to_artifact,
    expected_value_rows_to_artifact,
    load_expected_value_inputs,
)
from anomaly_science.decision.config import ExpectedValueConfig
from anomaly_science.strategy.metadata import strategy_metadata_run_config_rows


def run_mvp1_expected_value(
    *,
    state_path: str | Path,
    labels_path: str | Path,
    predictions_path: str | Path,
    out_dir: str | Path,
    config: ExpectedValueConfig | None = None,
) -> Path:
    """Run MVP1 pre-simulation expected-value analysis and write artifacts."""
    state_artifact_path = Path(state_path)
    labels_artifact_path = Path(labels_path)
    predictions_artifact_path = Path(predictions_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or ExpectedValueConfig()

    expected_value_rows = load_expected_value_inputs(
        state_path=state_artifact_path,
        labels_path=labels_artifact_path,
        predictions_path=predictions_artifact_path,
        config=cfg,
    )
    metric_rows = build_expected_value_metric_rows(expected_value_rows=expected_value_rows, config=cfg)
    protocol_rows = _protocol_rows(expected_value_row_count=len(expected_value_rows))
    run_config_rows = _run_config_rows(
        state_path=state_artifact_path,
        labels_path=labels_artifact_path,
        predictions_path=predictions_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_decision_timing.csv",
            expected_value_rows_to_artifact(expected_value_rows),
            get_artifact_schema("anomaly_decision_timing.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_ev_metrics.csv",
            expected_value_metric_rows_to_artifact(metric_rows),
            get_artifact_schema("anomaly_ev_metrics.csv"),
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


def _protocol_rows(*, expected_value_row_count: int) -> list[ProtocolAuditRow]:
    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_expected_value_scope",
            status=AuditStatus.PASS,
            message="pre-simulation expected-value analysis only; no entries, exits, realized PnL, trade simulation, shadow live, or production live",
        ),
        ProtocolAuditRow(
            check_name="state_label_prediction_schema_boundaries",
            status=AuditStatus.PASS,
            message="decision timing reads only strict anomaly_state_1m.csv, anomaly_outcome_labels.csv, and anomaly_oos_predictions.csv artifacts",
            artifact="anomaly_decision_timing.csv",
        ),
        ProtocolAuditRow(
            check_name="costs_included",
            status=AuditStatus.PASS,
            message="EV includes round-trip fee bps plus explicit slippage bps penalty",
            artifact="anomaly_decision_timing.csv",
        ),
        ProtocolAuditRow(
            check_name="no_trade_simulation_boundary",
            status=AuditStatus.PASS,
            message="entry price is a state-time reference for EV math only; no fill model, stop path, target path, or realized PnL is simulated",
            artifact="anomaly_decision_timing.csv",
        ),
        ProtocolAuditRow(
            check_name="decision_timing_rows_written",
            status=AuditStatus.PASS if expected_value_row_count > 0 else AuditStatus.WARN,
            message=f"wrote {expected_value_row_count} anomaly_decision_timing.csv rows",
            artifact="anomaly_decision_timing.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 expected value uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="expected_value_computed_before_trade_simulation",
            status=AuditStatus.PASS,
            message="decision stage computes EV from OOS probabilities, ATR-normalized distances, fees, and slippage before any trade simulation exists",
            artifact="anomaly_decision_timing.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(stage="mvp1_decision", implemented=implemented_methodology_rows)


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
    predictions_path: Path,
    output_path: Path,
    config: ExpectedValueConfig,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-expected-value", source="cli"),
        RunConfigRow(key="state_path", value=str(state_path), source="cli"),
        RunConfigRow(key="labels_path", value=str(labels_path), source="cli"),
        RunConfigRow(key="predictions_path", value=str(predictions_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        *runtime_reproducibility_rows(
            data_paths=(state_path, labels_path, predictions_path),
            config=config,
            extra_config={"command": "run-mvp1-expected-value", "stage": "mvp1_decision"},
        ),
        RunConfigRow(key="stage", value="mvp1_decision", source="runtime"),
        *strategy_metadata_run_config_rows(strategy_name=config.strategy_version),
        RunConfigRow(key="ev_version", value=config.ev_version, source="runtime"),
        RunConfigRow(key="target_horizon_minutes", value=str(config.target_horizon_minutes), source="runtime"),
        RunConfigRow(key="fee_bps", value=str(config.fee_bps), source="runtime"),
        RunConfigRow(key="slippage_bps", value=str(config.slippage_bps), source="runtime"),
        RunConfigRow(key="min_prediction_confidence", value=str(config.min_prediction_confidence), source="runtime"),
        RunConfigRow(key="min_rr", value=str(config.min_rr), source="runtime"),
        RunConfigRow(key="decision_scope", value="expected_value_not_trade_simulation", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-expected-value-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
