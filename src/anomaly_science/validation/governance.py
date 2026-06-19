from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.audit import build_methodology_v2_audit_rows
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.contracts.governance import HoldoutAccessLogRow, ResearchLedgerRow


def run_mvp1_holdout_governance(
    *,
    out_dir: str | Path,
    start_date: date,
    end_date: date,
    protocol_freeze_id: str,
    holdout_days: int = 60,
) -> Path:
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")
    if holdout_days <= 0:
        raise ValueError("holdout_days must be positive")
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    final_holdout_start = max(start_date, end_date - timedelta(days=holdout_days - 1))
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    ledger_rows = [
        ResearchLedgerRow(
            ledger_version="mvp1_research_ledger_v1",
            protocol_freeze_id=protocol_freeze_id,
            created_at_utc=created_at,
            research_stage="protocol_freeze_before_final_holdout",
            decision="final_holdout_locked_until_explicit_access_log",
            artifact="holdout_access_log.csv",
            rationale="Create governance artifacts before reading final holdout period.",
            holdout_access_allowed_after_freeze=True,
            final_holdout_start_date=final_holdout_start.isoformat(),
            final_holdout_end_date=end_date.isoformat(),
        )
    ]
    access_rows: tuple[HoldoutAccessLogRow, ...] = ()
    protocol_rows = _protocol_rows(ledger_row_count=len(ledger_rows), access_row_count=len(access_rows))
    run_config_rows = _run_config_rows(
        output_path=output_path,
        start_date=start_date,
        end_date=end_date,
        final_holdout_start=final_holdout_start,
        protocol_freeze_id=protocol_freeze_id,
        holdout_days=holdout_days,
    )

    written: list[Path] = []
    written.append(write_csv_artifact(output_path / "research_ledger.csv", [asdict(row) for row in ledger_rows], get_artifact_schema("research_ledger.csv")))
    written.append(write_csv_artifact(output_path / "holdout_access_log.csv", [asdict(row) for row in access_rows], get_artifact_schema("holdout_access_log.csv")))
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
    manifest = build_manifest(run_id="mvp1-holdout-governance-" + protocol_freeze_id, artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _protocol_rows(*, ledger_row_count: int, access_row_count: int) -> list[ProtocolAuditRow]:
    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_holdout_governance_scope",
            status=AuditStatus.PASS,
            message="governance artifacts only; no final holdout data is read by this stage",
        ),
        ProtocolAuditRow(
            check_name="research_ledger_written",
            status=AuditStatus.PASS,
            message=f"wrote {ledger_row_count} research_ledger.csv rows",
            artifact="research_ledger.csv",
        ),
        ProtocolAuditRow(
            check_name="holdout_access_log_initialized_empty",
            status=AuditStatus.PASS if access_row_count == 0 else AuditStatus.FAIL,
            message=f"holdout_access_log.csv initialized with {access_row_count} access rows before holdout read",
            artifact="holdout_access_log.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 holdout governance uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented = [
        ProtocolAuditRow(
            check_name="final_holdout_not_accessed_before_protocol_freeze",
            status=AuditStatus.PASS,
            message="protocol freeze ledger and empty access log are written before any holdout access can be recorded",
            artifact="holdout_access_log.csv",
        )
    ]
    return base_rows + build_methodology_v2_audit_rows(stage="mvp1_governance", implemented=implemented)


def _protocol_rows_to_artifact(rows: Sequence[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def _run_config_rows(
    *,
    output_path: Path,
    start_date: date,
    end_date: date,
    final_holdout_start: date,
    protocol_freeze_id: str,
    holdout_days: int,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-holdout-governance", source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        *runtime_reproducibility_rows(
            extra_config={
                "command": "run-mvp1-holdout-governance",
                "stage": "mvp1_governance",
                "protocol_freeze_id": protocol_freeze_id,
                "research_start_date": start_date.isoformat(),
                "research_end_date": end_date.isoformat(),
                "holdout_days": holdout_days,
            },
        ),
        RunConfigRow(key="stage", value="mvp1_governance", source="runtime"),
        RunConfigRow(key="protocol_freeze_id", value=protocol_freeze_id, source="cli"),
        RunConfigRow(key="research_start_date", value=start_date.isoformat(), source="cli"),
        RunConfigRow(key="research_end_date", value=end_date.isoformat(), source="cli"),
        RunConfigRow(key="holdout_days", value=str(holdout_days), source="cli"),
        RunConfigRow(key="final_holdout_start_date", value=final_holdout_start.isoformat(), source="runtime"),
        RunConfigRow(key="final_holdout_end_date", value=end_date.isoformat(), source="runtime"),
        RunConfigRow(key="holdout_scope", value="locked_until_explicit_access_log", source="runtime"),
    ]
