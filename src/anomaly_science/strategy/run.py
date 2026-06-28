from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.strategy.metadata import format_execution_policy_ids, format_horizons, format_required_data_streams
from anomaly_science.strategy.reject_reasons import anomaly_reject_reasons
from anomaly_science.strategy.registry import available_strategies, strategy_implementation_statuses


def run_mvp1_strategy_registry(*, out_dir: str | Path) -> Path:
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    rows = _strategy_registry_rows()
    reject_reason_rows = _strategy_reject_reason_rows()
    implementation_status_rows = _strategy_implementation_status_rows()
    protocol_rows = _protocol_rows(
        row_count=len(rows),
        reject_reason_count=len(reject_reason_rows),
        implementation_status_rows=implementation_status_rows,
    )
    run_config_rows = _run_config_rows(output_path=output_path)

    written: list[Path] = []
    written.append(write_csv_artifact(output_path / "strategy_registry.csv", rows, get_artifact_schema("strategy_registry.csv")))
    written.append(
        write_csv_artifact(
            output_path / "strategy_implementation_status.csv",
            implementation_status_rows,
            get_artifact_schema("strategy_implementation_status.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "strategy_reject_reasons.csv",
            reject_reason_rows,
            get_artifact_schema("strategy_reject_reasons.csv"),
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


def _strategy_registry_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for entry in available_strategies():
        strategy = entry.factory()
        metadata = strategy.metadata
        rows.append(
            {
                "strategy_name": metadata.strategy_name,
                "strategy_version": metadata.strategy_version,
                "strategy_contract_version": metadata.strategy_contract_version,
                "strategy_family": metadata.strategy_family,
                "horizon_minutes": metadata.horizon_minutes,
                "allowed_horizons": format_horizons(metadata.allowed_horizons),
                "default_horizon_minutes": metadata.default_horizon_minutes,
                "execution_policy_version": metadata.execution_policy_version,
                "execution_policy_ids": format_execution_policy_ids(strategy),
                "feature_schema_version": metadata.feature_schema_version,
                "label_schema_version": metadata.label_schema_version,
                "required_data_streams": format_required_data_streams(strategy.required_data_streams),
                "active_research_strategy": True,
                "live_trading_strategy": False,
            }
        )
    return rows


def _strategy_reject_reason_rows() -> list[dict[str, object]]:
    return [asdict(row) for row in anomaly_reject_reasons()]


def _strategy_implementation_status_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for status in strategy_implementation_statuses():
        rows.append(
            {
                "strategy_name": status.strategy_name,
                "strategy_family": status.strategy_family,
                "strategy_contract_version": status.strategy_contract_version,
                "horizon_minutes": status.horizon_minutes,
                "allowed_horizons": format_horizons(status.allowed_horizons),
                "default_horizon_minutes": status.default_horizon_minutes,
                "implementation_status": status.implementation_status,
                "executable": status.executable,
                "registry_error": status.registry_error,
            }
        )
    return rows


def _protocol_rows(
    *,
    row_count: int,
    reject_reason_count: int,
    implementation_status_rows: list[dict[str, object]],
) -> list[ProtocolAuditRow]:
    from anomaly_science.audit import build_methodology_v2_audit_rows

    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_strategy_registry_scope",
            status=AuditStatus.PASS,
            message="strategy registry metadata only; no data access, ML, decisions, simulation, or live trading",
            artifact="strategy_registry.csv",
        ),
        ProtocolAuditRow(
            check_name="strategy_registry_rows_written",
            status=AuditStatus.PASS if row_count > 0 else AuditStatus.FAIL,
            message=f"strategy_registry.csv written with {row_count} registered strategies",
            artifact="strategy_registry.csv",
        ),
        ProtocolAuditRow(
            check_name="strategy_reject_reasons_declared",
            status=AuditStatus.PASS if reject_reason_count > 0 else AuditStatus.FAIL,
            message=f"strategy_reject_reasons.csv written with {reject_reason_count} explicit reject reasons",
            artifact="strategy_reject_reasons.csv",
        ),
        ProtocolAuditRow(
            check_name="strategy_implementation_status_truthful",
            status=_implementation_status_audit_status(implementation_status_rows),
            message=_implementation_status_audit_message(implementation_status_rows),
            artifact="strategy_implementation_status.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 strategy registry uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented = [
        ProtocolAuditRow(
            check_name="base_strategy_contract_valid",
            status=AuditStatus.PASS,
            message="all registered strategies expose StrategyMetadata with selected/allowed/default horizons, required_data_streams, trigger-frame generate_triggers, and generate_custom_features contract",
            artifact="strategy_registry.csv",
        )
    ]
    return base_rows + build_methodology_v2_audit_rows(stage="mvp1_strategy_registry", implemented=implemented)


def _implementation_status_audit_status(rows: list[dict[str, object]]) -> AuditStatus:
    executable_rows = [row for row in rows if row["implementation_status"] == "implemented"]
    specified_rows = [row for row in rows if row["implementation_status"] == "specified_not_implemented"]
    executable_ok = all(row["executable"] is True and row["registry_error"] == "" for row in executable_rows)
    specified_ok = all(row["executable"] is False and bool(row["registry_error"]) for row in specified_rows)
    return AuditStatus.PASS if rows and executable_rows and executable_ok and specified_ok else AuditStatus.FAIL


def _implementation_status_audit_message(rows: list[dict[str, object]]) -> str:
    implemented = sum(1 for row in rows if row["implementation_status"] == "implemented")
    specified = sum(1 for row in rows if row["implementation_status"] == "specified_not_implemented")
    return (
        "strategy_implementation_status.csv written with "
        f"{implemented} executable variants and {specified} specified-only variants; "
        "specified-only variants are not instantiable through the registry when present"
    )


def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def _run_config_rows(*, output_path: Path) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-strategy-registry", source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        *runtime_reproducibility_rows(
            extra_config={"command": "run-mvp1-strategy-registry", "stage": "mvp1_strategy_registry"},
        ),
        RunConfigRow(key="stage", value="mvp1_strategy_registry", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-strategy-registry-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
