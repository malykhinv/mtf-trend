from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow
from anomaly_science.contracts.time import TemporalContractError, enforce_snapshot_contract


@dataclass(frozen=True, slots=True)
class TemporalAuditInput:
    check_name: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    future_start_time_ms: int | None = None


METHODOLOGY_V2_REQUIRED_CHECKS: tuple[str, ...] = (
    "technical_noise_shock_flag_computed_from_raw_timestamp_gaps",
    "technical_noise_shock_excluded_from_broad_detector",
    "technical_noise_shock_excluded_from_ml_train_validation_calibration_test",
    "ATR_1d_asof_t_computed_from_closed_past_candles",
    "fixed_percent_labels_forbidden",
    "intracandle_double_barrier_resolved_as_stop_loss_first",
    "relative_over_absolute_feature_contract_enforced",
    "market_shock_id_assigned",
    "simultaneous_anomalies_count_1m_point_in_time",
    "base_strategy_contract_valid",
    "purge_rule_snapshot_time_plus_Hmax_before_test_start",
    "weekly_walk_forward_heavy_models_enforced",
    "frozen_weekly_model_used_for_daily_oos",
)


METHODOLOGY_V2_CHECK_SET = frozenset(METHODOLOGY_V2_REQUIRED_CHECKS)


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


def build_methodology_v2_audit_rows(
    *,
    stage: str,
    implemented: Iterable[ProtocolAuditRow] = (),
) -> list[ProtocolAuditRow]:
    """Return one explicit methodology-v2 audit row for every required invariant.

    Stages may mark only checks they truly enforce as PASS/FAIL/WARN. Every other
    required methodology-v2 invariant is emitted as NOT_IMPLEMENTED, so a run
    cannot look protocol-complete simply because a check is absent.
    """
    if not stage:
        raise ValueError("stage is required")

    implemented_by_name: dict[str, ProtocolAuditRow] = {}
    for row in implemented:
        if row.check_name not in METHODOLOGY_V2_CHECK_SET:
            raise ValueError(f"unknown methodology-v2 check: {row.check_name}")
        if row.check_name in implemented_by_name:
            raise ValueError(f"duplicate methodology-v2 check: {row.check_name}")
        implemented_by_name[row.check_name] = row

    rows: list[ProtocolAuditRow] = []
    for check_name in METHODOLOGY_V2_REQUIRED_CHECKS:
        if check_name in implemented_by_name:
            rows.append(implemented_by_name[check_name])
            continue
        rows.append(
            ProtocolAuditRow(
                check_name=check_name,
                status=AuditStatus.NOT_IMPLEMENTED,
                message=(
                    f"methodology-v2 invariant is not implemented in {stage}; "
                    "downstream results must not be interpreted as fully protocol-valid"
                ),
            )
        )
    rows.append(build_protocol_gate_row(stage=stage, rows=rows))
    return rows


def build_protocol_gate_row(*, stage: str, rows: Iterable[ProtocolAuditRow]) -> ProtocolAuditRow:
    items = tuple(rows)
    fail_count = sum(1 for row in items if row.status is AuditStatus.FAIL)
    not_implemented_count = sum(1 for row in items if row.status is AuditStatus.NOT_IMPLEMENTED)
    if fail_count:
        return ProtocolAuditRow(
            check_name="protocol_interpretation_gate",
            status=AuditStatus.FAIL,
            message=f"{stage} has {fail_count} FAIL audit rows; downstream results must not be interpreted",
        )
    if not_implemented_count:
        return ProtocolAuditRow(
            check_name="protocol_interpretation_gate",
            status=AuditStatus.WARN,
            message=(
                f"{stage} has {not_implemented_count} NOT_IMPLEMENTED methodology rows; "
                "results are valid only for the implemented MVP scope"
            ),
        )
    return ProtocolAuditRow(
        check_name="protocol_interpretation_gate",
        status=AuditStatus.PASS,
        message=f"{stage} has no FAIL or NOT_IMPLEMENTED methodology rows",
    )
