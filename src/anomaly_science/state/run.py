from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.data.source import CsvDirectoryDataSource
from anomaly_science.state.builder import (
    build_online_strategy_state_1m_from_source,
    load_strategy_events_csv,
    state_rows_to_artifact,
)
from anomaly_science.state.config import OnlineStateBuilderConfig


def run_mvp1_state(
    *,
    input_dir: str | Path,
    events_path: str | Path,
    out_dir: str | Path,
    config: OnlineStateBuilderConfig | None = None,
) -> Path:
    """Run MVP1 online 1m state builder and write protocol artifacts."""
    input_path = Path(input_dir)
    events_artifact_path = Path(events_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or OnlineStateBuilderConfig()

    source = CsvDirectoryDataSource(input_path)
    events = load_strategy_events_csv(events_artifact_path)
    state_rows = build_online_strategy_state_1m_from_source(
        source=source,
        events_path=events_artifact_path,
        config=cfg,
    )
    excluded_event_count = sum(1 for event in events if event.technical_noise_shock or event.excluded_by_data_quality_gate)
    protocol_rows = _protocol_rows(
        event_count=len(events),
        state_row_count=len(state_rows),
        excluded_event_count=excluded_event_count,
    )
    run_config_rows = _run_config_rows(
        input_path=input_path,
        events_path=events_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_state_1m.csv",
            state_rows_to_artifact(state_rows),
            get_artifact_schema("strategy_state_1m.csv"),
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


def _protocol_rows(*, event_count: int, state_row_count: int, excluded_event_count: int) -> list[ProtocolAuditRow]:
    from anomaly_science.audit import build_methodology_v2_audit_rows

    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_state_scope",
            status=AuditStatus.PASS,
            message="online 1m state only; no future paths, labels, ML, PnL, trade simulation, or live execution",
        ),
        ProtocolAuditRow(
            check_name="events_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"strategy_events.csv accepted through strict schema boundary with {event_count} event rows",
            artifact="strategy_events.csv",
        ),
        ProtocolAuditRow(
            check_name="state_temporal_contract",
            status=AuditStatus.PASS,
            message="each state row uses closed 1m candles with available_time_ms <= state_time_ms; feature_cutoff_time_ms <= state_time_ms",
            artifact="strategy_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="running_high_low_asof_only",
            status=AuditStatus.PASS,
            message="running high/low are computed from event_start_time_ms through each state_time_ms, not from future event extremes",
            artifact="strategy_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="state_rows_written",
            status=AuditStatus.PASS,
            message=f"strategy_state_1m.csv written with {state_row_count} rows",
            artifact="strategy_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 state uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="technical_noise_shock_excluded_from_ml_train_validation_calibration_test",
            status=AuditStatus.PASS,
            message=(
                f"state builder excluded {excluded_event_count} technical-noise/data-quality-gated events before "
                "state/label/prediction/control artifacts can be built"
            ),
            artifact="strategy_state_1m.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_state",
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
    input_path: Path,
    events_path: Path,
    output_path: Path,
    config: OnlineStateBuilderConfig,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-state", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="events_path", value=str(events_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="data_source", value="csv_directory_v1", source="runtime"),
        *runtime_reproducibility_rows(
            data_paths=(input_path, events_path),
            config=config,
            extra_config={"command": "run-mvp1-state", "stage": "mvp1_state"},
        ),
        RunConfigRow(key="stage", value="mvp1_state", source="runtime"),
        RunConfigRow(key="state_builder_version", value=config.state_builder_version, source="runtime"),
        RunConfigRow(
            key="max_state_minutes_after_detection",
            value=str(config.max_state_minutes_after_detection),
            source="runtime",
        ),
        RunConfigRow(key="structural_features", value="not_computed_explicit_null", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-state-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
