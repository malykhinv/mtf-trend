from __future__ import annotations

from dataclasses import dataclass

from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow
from anomaly_science.contracts.time import TemporalContractError, enforce_snapshot_contract


@dataclass(frozen=True, slots=True)
class TemporalAuditInput:
    check_name: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    future_start_time_ms: int | None = None


def audit_temporal_contract(rows: list[TemporalAuditInput]) -> list[ProtocolAuditRow]:
    """Audit no-lookahead timing for a collection of rows."""
    results: list[ProtocolAuditRow] = []
    for row in rows:
        try:
            enforce_snapshot_contract(
                snapshot_time_ms=row.snapshot_time_ms,
                feature_cutoff_time_ms=row.feature_cutoff_time_ms,
                future_start_time_ms=row.future_start_time_ms,
            )
        except TemporalContractError as exc:
            results.append(
                ProtocolAuditRow(
                    check_name=row.check_name,
                    status=AuditStatus.FAIL,
                    message=str(exc),
                )
            )
        else:
            results.append(
                ProtocolAuditRow(
                    check_name=row.check_name,
                    status=AuditStatus.PASS,
                    message="temporal contract satisfied",
                )
            )
    return results
