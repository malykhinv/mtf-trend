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


ANOMALY_REJECT_REASONS: tuple[StrategyRejectReason, ...] = (
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "not_triggered", "mvp1_events", "trigger", "input row did not satisfy the anomaly trigger contract"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "technical_noise_shock", "mvp1_events", "trigger", "first candle after raw timestamp gap is excluded from trigger source"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "data_quality_fail", "mvp1_quality", "all_downstream", "critical data-quality failure invalidates downstream interpretation"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "insufficient_history_for_ATR", "mvp1_future", "labels", "ATR-normalized future paths cannot be built without enough prior history"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "insufficient_cross_section", "mvp1_features", "features", "point-in-time cross-section is too small for relative market features"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "missing_required_liquidation_data", "mvp1_features", "features", "liquidation-dependent features or ablations cannot be interpreted"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "missing_required_oi_data", "mvp1_features", "features", "open-interest-dependent features or ablations cannot be interpreted"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "horizon_not_available", "mvp1_labels", "prediction", "requested target horizon is not available for the row"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "future_path_incomplete", "mvp1_future", "labels", "future window after snapshot_time is incomplete"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "anti_binary_rule_failed", "mvp1_labels", "labels", "outcome cannot be represented by the registered multi-class nature labels"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "outside_strategy_lifecycle", "mvp1_state", "state", "snapshot is outside the registered anomaly lifecycle"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "RR_unacceptable", "mvp1_decision", "decision", "ATR-normalized reward/risk is below the strategy decision contract"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "calibrated_confidence_too_low", "mvp1_decision", "decision", "calibrated scenario probability is below decision threshold"),
    StrategyRejectReason("broad_anomaly_v1", "1.0.0", "systemic_cluster_guardrail", "mvp1_decision", "decision", "systemic cluster guardrail rejects symbol-specific alpha interpretation"),
)


def anomaly_reject_reasons() -> tuple[StrategyRejectReason, ...]:
    return ANOMALY_REJECT_REASONS


def anomaly_reject_reason_codes() -> frozenset[str]:
    return frozenset(row.reason_code for row in ANOMALY_REJECT_REASONS)
