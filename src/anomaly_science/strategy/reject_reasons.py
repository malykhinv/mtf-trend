from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StrategyRejectReason:
    strategy_name: str
    strategy_version: str
    reason_code: str
    owner_stage: str
    blocks_stage: str
    description: str


BROAD_ANOMALY_VARIANTS: tuple[str, ...] = (
    "broad_anomaly_v1_h15",
    "broad_anomaly_v1_h30",
    "broad_anomaly_v1_h60",
)

POST_ANOMALY_EXTENSION_VARIANTS: tuple[str, ...] = (
    "post_anomaly_extension_v1_h60",
    "post_anomaly_extension_v1_h120",
    "post_anomaly_extension_v1_h180",
)

POST_PUMP_DISTRIBUTION_VARIANTS: tuple[str, ...] = (
    "post_pump_distribution_v1_h60",
    "post_pump_distribution_v1_h120",
    "post_pump_distribution_v1_h180",
)

ANOMALY_EXECUTABLE_VARIANTS: tuple[str, ...] = (
    BROAD_ANOMALY_VARIANTS + POST_ANOMALY_EXTENSION_VARIANTS + POST_PUMP_DISTRIBUTION_VARIANTS
)

_REASON_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("not_triggered", "mvp1_events", "trigger", "input row did not satisfy the anomaly trigger contract"),
    ("technical_noise_shock", "mvp1_events", "trigger", "first candle after raw timestamp gap is excluded from trigger source"),
    ("warmup_after_data_gap", "mvp1_quality", "trigger", "rolling-context warm-up window after raw data gap blocks trigger generation"),
    ("cascade_suppressed", "mvp1_events", "dataset", "repeated same-symbol trigger inside active horizon is audit-only and cannot create dataset or simulation rows"),
    ("data_quality_fail", "mvp1_quality", "all_downstream", "critical data-quality failure invalidates downstream interpretation"),
    ("insufficient_history_for_ATR", "mvp1_future", "labels", "ATR-normalized future paths cannot be built without enough prior history"),
    ("insufficient_cross_section", "mvp1_features", "features", "point-in-time cross-section is too small for relative market features"),
    ("missing_required_liquidation_data", "mvp1_features", "features", "required liquidation stream missing for this strategy instance"),
    ("missing_required_oi_data", "mvp1_features", "features", "required open-interest stream missing for this strategy instance"),
    ("horizon_not_available", "mvp1_labels", "prediction", "requested target horizon is not available for the row"),
    ("future_path_incomplete", "mvp1_future", "labels", "future window after snapshot_time is incomplete"),
    ("anti_binary_rule_failed", "mvp1_labels", "labels", "outcome cannot be represented by the registered multi-class nature labels"),
    ("causality_gate_failed", "mvp1_features", "features", "custom strategy features are not stable under point-in-time truncation"),
    ("outside_strategy_lifecycle", "mvp1_state", "state", "snapshot is outside the registered anomaly lifecycle"),
    ("RR_unacceptable", "mvp1_decision", "decision", "ATR-normalized reward/risk is below the strategy decision contract"),
    ("calibrated_confidence_too_low", "mvp1_decision", "decision", "calibrated scenario probability is below decision threshold"),
    ("systemic_cluster_guardrail", "mvp1_decision", "decision", "systemic cluster guardrail rejects symbol-specific alpha interpretation"),
)


ANOMALY_REJECT_REASONS: tuple[StrategyRejectReason, ...] = tuple(
    StrategyRejectReason(strategy_name, "1.0.0", reason_code, owner_stage, blocks_stage, description)
    for strategy_name in ANOMALY_EXECUTABLE_VARIANTS
    for reason_code, owner_stage, blocks_stage, description in _REASON_SPECS
)


def anomaly_reject_reasons() -> tuple[StrategyRejectReason, ...]:
    return ANOMALY_REJECT_REASONS


def anomaly_reject_reason_codes() -> frozenset[str]:
    return frozenset(row.reason_code for row in ANOMALY_REJECT_REASONS)
