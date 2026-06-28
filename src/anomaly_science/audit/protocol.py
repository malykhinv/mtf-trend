from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow
from anomaly_science.contracts.horizons import (
    research_horizon_label_column,
    validate_supported_research_horizon,
)
from anomaly_science.contracts.time import TemporalContractError, enforce_snapshot_contract
from anomaly_science.strategy.metadata import active_strategy_h_max_minutes
from anomaly_science.strategy.registry import StrategyRegistryError, get_strategy, validate_strategy_horizon


@dataclass(frozen=True, slots=True)
class TemporalAuditInput:
    check_name: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    future_start_time_ms: int | None = None


METHODOLOGY_V2_REQUIRED_CHECKS: tuple[str, ...] = (
    "technical_noise_shock_flag_computed_from_raw_timestamp_gaps",
    "technical_noise_shock_excluded_from_broad_detector",
    "warmup_window_excluded_from_trigger_generation",
    "data_quality_mask_enforced_before_trigger_generation",
    "required_data_streams_applied_before_trigger_generation",
    "trigger_cascade_suppressed_before_dataset_and_simulation",
    "technical_noise_shock_excluded_from_ml_train_validation_calibration_test",
    "ATR_1d_asof_t_computed_from_closed_past_candles",
    "fixed_percent_labels_forbidden",
    "fixed_percent_stop_target_forbidden",
    "partial_target_fraction_grid_declared_by_strategy",
    "intracandle_double_barrier_resolved_as_stop_loss_first",
    "relative_over_absolute_feature_contract_enforced",
    "custom_features_causality_gate_enforced",
    "market_shock_id_assigned",
    "simultaneous_anomalies_count_1m_point_in_time",
    "base_strategy_contract_valid",
    "purge_rule_snapshot_time_plus_Hmax_before_test_start",
    "horizon_core_whitelist_enforced",
    "horizon_strategy_allowed_horizon_enforced",
    "horizon_target_label_column_matches_target_horizon",
    "horizon_active_hmax_matches_active_strategy_horizons",
    "horizon_purge_uses_active_hmax",
    "horizon_prediction_artifact_identity_consistent",
    "horizon_model_metadata_identity_consistent",
    "weekly_walk_forward_heavy_models_enforced",
    "frozen_weekly_model_used_for_daily_oos",
    "calibration_breakdowns_written",
    "sample_weight_policy_explicit_and_asof_safe",
    "expected_value_computed_before_trade_simulation",
    "execution_reference_model_aligned_between_ev_and_simulation",
    "trade_simulation_after_calibration_and_decision_timing",
    "pessimistic_entry_price_includes_slippage_penalty",
    "anti_pyramiding_one_open_position_per_symbol_strategy",
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
        "warmup_window_excluded_from_trigger_generation",
        "data_quality_mask_enforced_before_trigger_generation",
        "required_data_streams_applied_before_trigger_generation",
        "trigger_cascade_suppressed_before_dataset_and_simulation",
        "base_strategy_contract_valid",
    ),
    "mvp1_strategy_registry": (
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
    "custom_features_causality_gate_enforced",
    ),
    "mvp1_feature_matrix": (
        "ATR_1d_asof_t_computed_from_closed_past_candles",
        "relative_over_absolute_feature_contract_enforced",
    "custom_features_causality_gate_enforced",
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
        "horizon_core_whitelist_enforced",
        "horizon_strategy_allowed_horizon_enforced",
        "horizon_target_label_column_matches_target_horizon",
        "horizon_active_hmax_matches_active_strategy_horizons",
        "horizon_purge_uses_active_hmax",
        "horizon_prediction_artifact_identity_consistent",
        "horizon_model_metadata_identity_consistent",
        "weekly_walk_forward_heavy_models_enforced",
        "frozen_weekly_model_used_for_daily_oos",
        "calibration_breakdowns_written",
        "sample_weight_policy_explicit_and_asof_safe",
    ),
    "mvp1_controls": (
        "technical_noise_shock_excluded_from_ml_train_validation_calibration_test",
        "fixed_percent_labels_forbidden",
        "purge_rule_snapshot_time_plus_Hmax_before_test_start",
    ),
    "mvp1_decision": (
        "expected_value_computed_before_trade_simulation",
        "execution_reference_model_aligned_between_ev_and_simulation",
        "fixed_percent_stop_target_forbidden",
    ),
    "mvp1_simulation": (
        "trade_simulation_after_calibration_and_decision_timing",
        "execution_reference_model_aligned_between_ev_and_simulation",
        "pessimistic_entry_price_includes_slippage_penalty",
        "anti_pyramiding_one_open_position_per_symbol_strategy",
        "intracandle_double_barrier_resolved_as_stop_loss_first",
        "fixed_percent_stop_target_forbidden",
        "partial_target_fraction_grid_declared_by_strategy",
    ),
    "mvp1_governance": (
        "final_holdout_not_accessed_before_protocol_freeze",
    ),
}


def build_horizon_consistency_audit_rows(
    *,
    stage: str,
    strategy_name: str,
    target_horizon_minutes: int,
    target_label_column: str,
    active_strategy_names: Iterable[str],
    active_h_max_minutes: int,
    purge_horizon_minutes: int | None = None,
    prediction_rows: Iterable[object] = (),
    model_metadata_rows: Iterable[object] = (),
) -> list[ProtocolAuditRow]:
    """Build independent horizon-consistency audit rows.

    These checks intentionally read only explicit config/artifact identity
    fields. They do not trust a pipeline stage simply because the stage ran.
    """
    if not stage:
        raise ValueError("stage is required")
    active_names = tuple(active_strategy_names)
    predictions = tuple(prediction_rows)
    metadata_rows = tuple(model_metadata_rows)
    expected_label_column = ""
    rows: list[ProtocolAuditRow] = []

    def add(check_name: str, status: AuditStatus, message: str, artifact: str = "strategy_protocol_audit.csv") -> None:
        rows.append(ProtocolAuditRow(check_name=check_name, status=status, message=message, artifact=artifact))

    try:
        validate_supported_research_horizon(target_horizon_minutes, field_name="target_horizon_minutes")
        expected_label_column = research_horizon_label_column(target_horizon_minutes)
    except ValueError as exc:
        add(
            "horizon_core_whitelist_enforced",
            AuditStatus.FAIL,
            f"target horizon is outside the Core-supported research horizon whitelist: {exc}",
        )
    else:
        add(
            "horizon_core_whitelist_enforced",
            AuditStatus.PASS,
            f"target horizon h{target_horizon_minutes} is in the Core-supported research horizon whitelist",
        )

    try:
        validate_strategy_horizon(strategy_name, target_horizon_minutes)
        metadata = get_strategy(strategy_name).metadata
        if metadata.default_horizon_minutes not in metadata.allowed_horizons:
            raise StrategyRegistryError(
                f"default horizon h{metadata.default_horizon_minutes} is not in allowed_horizons={metadata.allowed_horizons}"
            )
    except (StrategyRegistryError, ValueError) as exc:
        add(
            "horizon_strategy_allowed_horizon_enforced",
            AuditStatus.FAIL,
            f"strategy/horizon pair is not registry-valid: {exc}",
        )
    else:
        add(
            "horizon_strategy_allowed_horizon_enforced",
            AuditStatus.PASS,
            (
                f"strategy {strategy_name!r} allows selected horizon h{target_horizon_minutes}; "
                f"default h{metadata.default_horizon_minutes} is inside allowed_horizons={metadata.allowed_horizons}"
            ),
        )

    if expected_label_column and target_label_column == expected_label_column:
        add(
            "horizon_target_label_column_matches_target_horizon",
            AuditStatus.PASS,
            f"target_label_column={target_label_column!r} matches h{target_horizon_minutes}",
        )
    else:
        add(
            "horizon_target_label_column_matches_target_horizon",
            AuditStatus.FAIL,
            (
                f"target_label_column={target_label_column!r} does not match "
                f"expected={expected_label_column!r} for h{target_horizon_minutes}"
            ),
        )

    try:
        expected_h_max = active_strategy_h_max_minutes(active_names)
    except (StrategyRegistryError, ValueError) as exc:
        add(
            "horizon_active_hmax_matches_active_strategy_horizons",
            AuditStatus.FAIL,
            f"active strategy H_max cannot be computed from active_strategy_names={active_names!r}: {exc}",
        )
        expected_h_max = None
    else:
        if active_h_max_minutes == expected_h_max:
            add(
                "horizon_active_hmax_matches_active_strategy_horizons",
                AuditStatus.PASS,
                f"active_h_max_minutes={active_h_max_minutes} equals max selected horizons from active_strategy_names={active_names!r}",
            )
        else:
            add(
                "horizon_active_hmax_matches_active_strategy_horizons",
                AuditStatus.FAIL,
                f"active_h_max_minutes={active_h_max_minutes} but active_strategy_names imply H_max={expected_h_max}",
            )

    if purge_horizon_minutes is None:
        add(
            "horizon_purge_uses_active_hmax",
            AuditStatus.NOT_IMPLEMENTED,
            "purge horizon was not provided to the horizon audit",
        )
    elif expected_h_max is not None and purge_horizon_minutes == expected_h_max and purge_horizon_minutes == active_h_max_minutes:
        add(
            "horizon_purge_uses_active_hmax",
            AuditStatus.PASS,
            f"purge_horizon_minutes={purge_horizon_minutes} equals active H_max",
        )
    else:
        add(
            "horizon_purge_uses_active_hmax",
            AuditStatus.FAIL,
            (
                f"purge_horizon_minutes={purge_horizon_minutes} must equal active_h_max_minutes={active_h_max_minutes} "
                f"and computed H_max={expected_h_max}"
            ),
        )

    prediction_errors = _horizon_identity_errors(
        artifact_rows=predictions,
        strategy_name=strategy_name,
        target_horizon_minutes=target_horizon_minutes,
        target_label_column=target_label_column,
        active_h_max_minutes=active_h_max_minutes,
    )
    if prediction_errors:
        add(
            "horizon_prediction_artifact_identity_consistent",
            AuditStatus.FAIL,
            "; ".join(prediction_errors[:5]),
            artifact="strategy_oos_predictions.csv",
        )
    elif predictions:
        add(
            "horizon_prediction_artifact_identity_consistent",
            AuditStatus.PASS,
            f"{len(predictions)} prediction rows carry matching strategy/horizon identity fields",
            artifact="strategy_oos_predictions.csv",
        )
    else:
        add(
            "horizon_prediction_artifact_identity_consistent",
            AuditStatus.WARN,
            "no prediction rows were available for artifact horizon identity verification",
            artifact="strategy_oos_predictions.csv",
        )

    metadata_errors = _horizon_identity_errors(
        artifact_rows=metadata_rows,
        strategy_name=strategy_name,
        target_horizon_minutes=target_horizon_minutes,
        target_label_column=target_label_column,
        active_h_max_minutes=active_h_max_minutes,
    )
    if metadata_errors:
        add(
            "horizon_model_metadata_identity_consistent",
            AuditStatus.FAIL,
            "; ".join(metadata_errors[:5]),
            artifact="strategy_model_metadata.csv",
        )
    elif metadata_rows:
        add(
            "horizon_model_metadata_identity_consistent",
            AuditStatus.PASS,
            f"{len(metadata_rows)} model metadata rows carry matching strategy/horizon identity fields",
            artifact="strategy_model_metadata.csv",
        )
    else:
        add(
            "horizon_model_metadata_identity_consistent",
            AuditStatus.WARN,
            "no model metadata rows were available for artifact horizon identity verification",
            artifact="strategy_model_metadata.csv",
        )

    return rows


def _horizon_identity_errors(
    *,
    artifact_rows: Iterable[object],
    strategy_name: str,
    target_horizon_minutes: int,
    target_label_column: str,
    active_h_max_minutes: int,
) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(artifact_rows):
        for field_name, expected in (
            ("strategy_name", strategy_name),
            ("target_horizon_minutes", target_horizon_minutes),
            ("target_label_column", target_label_column),
            ("active_h_max_minutes", active_h_max_minutes),
        ):
            actual = getattr(row, field_name, None)
            if actual != expected:
                errors.append(f"row {index} {field_name}={actual!r}, expected {expected!r}")
    return errors

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
