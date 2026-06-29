from __future__ import annotations

import csv
import os
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.artifacts.writer import ArtifactWriteError, link_or_copy_identical_artifact
from anomaly_science.contracts.artifacts import ArtifactSchema, get_artifact_schema, get_strategy_artifact_companion_names
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.contracts.decision import ExpectedValueRow
from anomaly_science.decision.builder import (
    build_expected_value_metric_rows,
    expected_value_metric_rows_to_artifact,
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
    decision_written, decision_row_count = _write_decision_timing_rows_with_aliases(
        output_path / "strategy_decision_timing.csv",
        expected_value_rows,
        get_artifact_schema("strategy_decision_timing.csv"),
    )
    written.extend(decision_written)
    if decision_row_count != len(expected_value_rows):
        raise ArtifactWriteError("strategy_decision_timing.csv row count changed during write")
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_ev_metrics.csv",
            expected_value_metric_rows_to_artifact(metric_rows),
            get_artifact_schema("strategy_ev_metrics.csv"),
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


def _write_decision_timing_rows_with_aliases(
    path: Path,
    rows: Iterable[ExpectedValueRow],
    schema: ArtifactSchema,
) -> tuple[list[Path], int]:
    if path.name != schema.name:
        raise ArtifactWriteError(f"path name {path.name!r} does not match schema name {schema.name!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    row_count = _write_decision_timing_rows(path=path, rows=rows, schema=schema)
    written = [path]
    for alias_name in get_strategy_artifact_companion_names(schema.name):
        alias_path = path.with_name(alias_name)
        alias_schema = get_artifact_schema(alias_name)
        if tuple(alias_schema.required_columns) != tuple(schema.required_columns):
            raise ArtifactWriteError(f"{schema.name} streaming alias {alias_name} must have identical columns")
        link_or_copy_identical_artifact(path, alias_path)
        written.append(alias_path)
    return written, row_count


def _write_decision_timing_rows(*, path: Path, rows: Iterable[ExpectedValueRow], schema: ArtifactSchema) -> int:
    fieldnames = list(schema.required_columns)
    attribute_names = [_decision_timing_attribute_name(fieldname) for fieldname in fieldnames]
    row_field_names = {field.name for field in fields(ExpectedValueRow)}
    missing = [name for name in attribute_names if name not in row_field_names]
    if missing:
        raise ArtifactWriteError(f"strategy_decision_timing.csv schema has unknown row fields: {missing}")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    row_count = 0
    with tmp_path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.writer(file_obj)
        writer.writerow(fieldnames)
        for row in rows:
            writer.writerow(_decision_timing_row_values_for_attributes(row=row, attribute_names=attribute_names))
            row_count += 1
            if row_count % 100_000 == 0:
                file_obj.flush()
    os.replace(tmp_path, path)
    return row_count


def _decision_timing_row_values_for_attributes(*, row: ExpectedValueRow, attribute_names: list[str]) -> list[object]:
    return ["" if (value := getattr(row, attribute_name)) is None else value for attribute_name in attribute_names]


def _decision_timing_attribute_name(fieldname: str) -> str:
    if fieldname == "ATR_1d_asof_t":
        return "core_atr_1440"
    return fieldname


def _protocol_rows(*, expected_value_row_count: int) -> list[ProtocolAuditRow]:
    from anomaly_science.audit import build_methodology_v2_audit_rows

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
            message="Nature-proxy EV includes round-trip fee bps plus explicit slippage bps penalty",
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
            message="decision stage computes nature-proxy utility from OOS probabilities, point-in-time structural distances, fees, and slippage before any trade simulation exists",
            artifact="anomaly_decision_timing.csv",
        ),
        ProtocolAuditRow(
            check_name="nature_proxy_utility_marked_non_final",
            status=AuditStatus.PASS,
            message="decision artifacts explicitly mark utility_model_kind=nature_proxy, utility_evidence_status=NON_FINAL, and utility_evidence_claim_allowed=false until realized barrier outcomes exist",
            artifact="anomaly_decision_timing.csv",
        ),
        ProtocolAuditRow(
            check_name="execution_reference_model_aligned_between_ev_and_simulation",
            status=AuditStatus.PASS,
            message="EV rows record the shared execution_reference_model and use a snapshot current-close proxy for the later next-open pessimistic simulation model",
            artifact="anomaly_decision_timing.csv",
        ),
        ProtocolAuditRow(
            check_name="fixed_percent_stop_target_forbidden",
            status=AuditStatus.PASS,
            message="EV resolves stop/target prices only from strategy-declared point-in-time structural anchors; ATR and fixed-percent exits are forbidden",
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
        *strategy_metadata_run_config_rows(strategy_name=config.strategy_name),
        RunConfigRow(key="ev_version", value=config.ev_version, source="runtime"),
        RunConfigRow(key="utility_model_kind", value=config.utility_model_kind, source="runtime"),
        RunConfigRow(key="utility_evidence_status", value=config.utility_evidence_status, source="runtime"),
        RunConfigRow(key="utility_evidence_claim_allowed", value=str(config.utility_evidence_claim_allowed).lower(), source="runtime"),
        RunConfigRow(key="target_horizon_minutes", value=str(config.target_horizon_minutes), source="runtime"),
        RunConfigRow(key="execution_reference_model", value=config.execution_reference_model, source="runtime"),
        RunConfigRow(key="entry_price_basis", value=config.entry_price_basis, source="runtime"),
        RunConfigRow(key="cost_model", value=config.cost_model, source="runtime"),
        RunConfigRow(key="fee_bps", value=str(config.fee_bps), source="runtime"),
        RunConfigRow(key="slippage_bps", value=str(config.slippage_bps), source="runtime"),
        RunConfigRow(key="min_prediction_confidence", value=str(config.min_prediction_confidence), source="runtime"),
        RunConfigRow(key="min_rr", value=str(config.min_rr), source="runtime"),
        RunConfigRow(key="decision_scope", value="nature_proxy_utility_not_trade_simulation", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-expected-value-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
