from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.labels.builder import (
    build_strategy_outcome_labels_from_inputs,
    load_outcome_label_inputs,
    outcome_label_rows_to_artifact,
)
from anomaly_science.labels.config import OutcomeLabelConfig


def run_mvp1_labels(
    *,
    state_path: str | Path,
    future_path: str | Path,
    out_dir: str | Path,
    config: OutcomeLabelConfig | None = None,
) -> Path:
    """Run MVP1 future-nature scenario label builder and write artifacts."""
    state_artifact_path = Path(state_path)
    future_artifact_path = Path(future_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or OutcomeLabelConfig()

    inputs = load_outcome_label_inputs(state_path=state_artifact_path, future_path=future_artifact_path)
    label_rows = build_strategy_outcome_labels_from_inputs(inputs=inputs, config=cfg)
    protocol_rows = _protocol_rows(input_row_count=len(inputs), label_row_count=len(label_rows))
    run_config_rows = _run_config_rows(
        state_path=state_artifact_path,
        future_path=future_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_outcome_labels.csv",
            outcome_label_rows_to_artifact(label_rows),
            get_artifact_schema("strategy_outcome_labels.csv"),
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


def _protocol_rows(*, input_row_count: int, label_row_count: int) -> list[ProtocolAuditRow]:
    from anomaly_science.audit import build_methodology_v2_audit_rows

    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_labels_scope",
            status=AuditStatus.PASS,
            message="descriptive future-nature scenario labels only; no ML models, PnL, trade simulation, decision rules, shadow live, or production live",
        ),
        ProtocolAuditRow(
            check_name="state_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_state_1m.csv accepted through strict schema boundary for {input_row_count} label input rows",
            artifact="anomaly_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="future_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_future_paths.csv accepted through strict schema boundary for {input_row_count} label input rows",
            artifact="anomaly_future_paths.csv",
        ),
        ProtocolAuditRow(
            check_name="outcome_label_temporal_contract",
            status=AuditStatus.PASS,
            message="label join preserves feature_cutoff_time_ms <= snapshot_time_ms < future_start_time_ms for every row",
            artifact="anomaly_outcome_labels.csv",
        ),
        ProtocolAuditRow(
            check_name="labels_use_future_paths_only",
            status=AuditStatus.PASS,
            message="scenario values are derived only from ATR-normalized future path fields; state rows are used only for strict join and temporal audit",
            artifact="anomaly_outcome_labels.csv",
        ),
        ProtocolAuditRow(
            check_name="missing_future_is_data_condition",
            status=AuditStatus.PASS,
            message="missing_future is explicit and not collapsed into unclear, static_or_chop, or a tradeable class",
            artifact="anomaly_outcome_labels.csv",
        ),
        ProtocolAuditRow(
            check_name="label_rows_written",
            status=AuditStatus.PASS,
            message=f"wrote {label_row_count} anomaly_outcome_labels.csv rows",
            artifact="anomaly_outcome_labels.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 labels use anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="ATR_1d_asof_t_computed_from_closed_past_candles",
            status=AuditStatus.PASS,
            message="labels consume internal core_atr_1440 from future paths, which is computed strictly as-of snapshot_time_ms; CSV alias is ATR_1d_asof_t",
            artifact="anomaly_outcome_labels.csv",
        ),
        ProtocolAuditRow(
            check_name="fixed_percent_labels_forbidden",
            status=AuditStatus.PASS,
            message="OutcomeLabelConfig exposes only ATR-unit thresholds k_continuation, k_fade, and k_chop; fixed-percent label thresholds are not part of the label schema",
            artifact="anomaly_outcome_labels.csv",
        ),
        ProtocolAuditRow(
            check_name="intracandle_double_barrier_resolved_as_stop_loss_first",
            status=AuditStatus.PASS,
            message="label builder maps stop_loss_first double-barrier rows to unclear and never credits a profit-label for same-candle target/stop collisions",
            artifact="anomaly_outcome_labels.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_labels",
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
    future_path: Path,
    output_path: Path,
    config: OutcomeLabelConfig,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-labels", source="cli"),
        RunConfigRow(key="state_path", value=str(state_path), source="cli"),
        RunConfigRow(key="future_path", value=str(future_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        *runtime_reproducibility_rows(
            data_paths=(state_path, future_path),
            config=config,
            extra_config={"command": "run-mvp1-labels", "stage": "mvp1_labels"},
        ),
        RunConfigRow(key="stage", value="mvp1_labels", source="runtime"),
        RunConfigRow(key="label_schema_version", value=config.label_schema_version, source="runtime"),
        RunConfigRow(
            key="horizons_minutes",
            value="|".join(str(horizon_minutes) for horizon_minutes in config.horizons_minutes),
            source="runtime",
        ),
        RunConfigRow(key="atr_window_minutes", value=str(config.atr_window_minutes), source="runtime"),
        RunConfigRow(key="k_continuation", value=str(config.k_continuation), source="runtime"),
        RunConfigRow(key="k_fade", value=str(config.k_fade), source="runtime"),
        RunConfigRow(key="k_chop", value=str(config.k_chop), source="runtime"),
        RunConfigRow(key="trap_policy", value=config.trap_policy, source="runtime"),
        RunConfigRow(key="scenario_labels", value="atr_normalized_4class_descriptive_not_trading_labels", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-labels-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
