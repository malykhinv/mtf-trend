from __future__ import annotations

from dataclasses import dataclass

from anomaly_science.contracts.horizons import research_horizon_label_column
from anomaly_science.contracts.decision import UTILITY_EVIDENCE_STATUS_NON_FINAL, UTILITY_MODEL_KIND_NATURE_PROXY
from anomaly_science.contracts.execution import EV_ENTRY_PRICE_BASIS, EV_EXECUTION_REFERENCE_MODEL, ROUND_TRIP_COST_MODEL
from anomaly_science.strategy.registry import validate_strategy_horizon


@dataclass(frozen=True, slots=True)
class ExpectedValueConfig:
    """Configuration for the pre-simulation expected-value decision layer."""

    ev_version: str = "mvp1_expected_value_oos_calibrated_proxy_v1"
    utility_model_kind: str = UTILITY_MODEL_KIND_NATURE_PROXY
    utility_evidence_status: str = UTILITY_EVIDENCE_STATUS_NON_FINAL
    utility_evidence_claim_allowed: bool = False
    strategy_name: str = "broad_anomaly_v1_h30"
    target_horizon_minutes: int = 30
    fee_bps: float = 4.0
    slippage_bps: float = 2.0
    execution_reference_model: str = EV_EXECUTION_REFERENCE_MODEL
    entry_price_basis: str = EV_ENTRY_PRICE_BASIS
    cost_model: str = ROUND_TRIP_COST_MODEL
    min_prediction_confidence: float = 0.40
    min_rr: float = 1.0

    def __post_init__(self) -> None:
        if not self.ev_version:
            raise ValueError("ev_version is required")
        if self.utility_model_kind != UTILITY_MODEL_KIND_NATURE_PROXY:
            raise ValueError("utility_model_kind must be nature_proxy for MVP1 EV")
        if self.utility_evidence_status != UTILITY_EVIDENCE_STATUS_NON_FINAL:
            raise ValueError("utility_evidence_status must be NON_FINAL until realized barrier outcome modeling exists")
        if self.utility_evidence_claim_allowed:
            raise ValueError("nature-proxy EV cannot allow final utility evidence claims")
        if not self.strategy_name:
            raise ValueError("strategy_name is required")
        validate_strategy_horizon(self.strategy_name, self.target_horizon_minutes)
        if self.fee_bps < 0.0:
            raise ValueError("fee_bps must be non-negative")
        if self.slippage_bps < 0.0:
            raise ValueError("slippage_bps must be non-negative")
        if self.execution_reference_model != EV_EXECUTION_REFERENCE_MODEL:
            raise ValueError("execution_reference_model must match EV_EXECUTION_REFERENCE_MODEL")
        if self.entry_price_basis != EV_ENTRY_PRICE_BASIS:
            raise ValueError("entry_price_basis must match EV_ENTRY_PRICE_BASIS")
        if self.cost_model != ROUND_TRIP_COST_MODEL:
            raise ValueError("cost_model must match ROUND_TRIP_COST_MODEL")
        if self.min_prediction_confidence < 0.0 or self.min_prediction_confidence > 1.0:
            raise ValueError("min_prediction_confidence must be within [0, 1]")
        if self.min_rr <= 0.0:
            raise ValueError("min_rr must be positive")

    @property
    def target_label_column(self) -> str:
        return research_horizon_label_column(self.target_horizon_minutes)
