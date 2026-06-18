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
    "expected_value_computed_before_trade_simulation",
    "trade_simulation_after_calibration_and_decision_timing",
    "pessimistic_entry_price_includes_slippage_penalty",
    "final_holdout_not_accessed_before_protocol_freeze",
)


METHODOLOGY_V2_CHECK_SET = frozenset(METHODOLOGY_V2_REQUIRED_CHECKS)


METHODOLOGY_V2_STAGE_REQUIRED_CHECKS: dict[str, tuple[str, ...]] = {
    "mvp1_data_audit": (
        "technical_noise_shock_flag_computed_from_raw_timestamp_gaps",
    ),
    "mvp1_events": (
        "technical_noise_shock_flag_computed_from_raw_timestamp_gaps",
        "technical_noise_shock_excluded_from_broad_detector",
        "base_strategy_contract_valid",
    ),
    "mvp1_state": (
        "technical_noise_shock_excluded_from_ml_train_validation_calibration_test",
    ),
    "mvp1_future": (
        "ATR_1d_asof_t_computed_from_closed_past_candles",
        "fixed_percent_labels_forbidden",
        "intracandle_double_barrier_resolved_as_stop_loss_first",
    ),
    "mvp1_feature_catalog": (
        "relative_over_absolute_feature_contract_enforced",
    ),
    "mvp1_feature_matrix": (
        "ATR_1d_asof_t_computed_from_closed_past_candles",
        "relative_over_absolute_feature_contract_enforced",
        "market_shock_id_assigned",
        "simultaneous_anomalies_count_1m_point_in_time",
    ),
    "mvp1_atlas": (
        "market_shock_id_assigned",
    ),
    "mvp1_labels": (
        "ATR_1d_asof_t_computed_from_closed_past_candles",
        "fixed_percent_labels_forbidden",
        "intracandle_double_barrier_resolved_as_stop_loss_first",
    ),
    "mvp1_prediction": (
        "technical_noise_shock_excluded_from_ml_train_validation_calibration_test",
        "fixed_percent_labels_forbidden",
        "purge_rule_snapshot_time_plus_Hmax_before_test_start",
        "weekly_walk_forward_heavy_models_enforced",
        "frozen_weekly_model_used_for_daily_oos",
    ),
    "mvp1_controls": (
        "technical_noise_shock_excluded_from_ml_train_validation_calibration_test",
        "fixed_percent_labels_forbidden",
        "purge_rule_snapshot_time_plus_Hmax_before_test_start",
    ),
    "mvp1_decision": (
        "expected_value_computed_before_trade_simulation",
    ),
    "mvp1_simulation": (
        "trade_simulation_after_calibration_and_decision_timing",
        "pessimistic_entry_price_includes_slippage_penalty",
        "intracandle_double_barrier_resolved_as_stop_loss_first",
    ),
    "mvp1_governance": (
        "final_holdout_not_accessed_before_protocol_freeze",
    ),
}


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
    required_checks: Iterable[str] | None = None,
) -> list[ProtocolAuditRow]:
    """Return explicit methodology-v2 audit rows for checks relevant to a stage.

    The methodology has one global check catalog, but individual stages own only
    the subset they can prove directly. Unknown stages default to the full set so
    new stages cannot accidentally hide missing protocol gates.
    """
    if not stage:
        raise ValueError("stage is required")
    stage_required_checks = tuple(
        required_checks
        if required_checks is not None
        else METHODOLOGY_V2_STAGE_REQUIRED_CHECKS.get(stage, METHODOLOGY_V2_REQUIRED_CHECKS)
    )
    for check_name in stage_required_checks:
        if check_name not in METHODOLOGY_V2_CHECK_SET:
            raise ValueError(f"unknown methodology-v2 check: {check_name}")

    implemented_by_name: dict[str, ProtocolAuditRow] = {}
    for row in implemented:
        if row.check_name not in METHODOLOGY_V2_CHECK_SET:
            raise ValueError(f"unknown methodology-v2 check: {row.check_name}")
        if row.check_name not in stage_required_checks:
            raise ValueError(f"methodology-v2 check {row.check_name} is not required for stage {stage}")
        if row.check_name in implemented_by_name:
            raise ValueError(f"duplicate methodology-v2 check: {row.check_name}")
        implemented_by_name[row.check_name] = row

    rows: list[ProtocolAuditRow] = []
    for check_name in stage_required_checks:
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
