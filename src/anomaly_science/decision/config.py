from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExpectedValueConfig:
    """Configuration for the pre-simulation expected-value decision layer."""

    ev_version: str = "mvp1_expected_value_oos_calibrated_proxy_v1"
    strategy_name: str = "anomaly"
    strategy_version: str = "broad_anomaly_v1_h30"
    target_horizon_minutes: int = 30
    fee_bps: float = 4.0
    slippage_bps: float = 2.0
    min_prediction_confidence: float = 0.40
    min_rr: float = 1.0

    def __post_init__(self) -> None:
        if not self.ev_version:
            raise ValueError("ev_version is required")
        if not self.strategy_name:
            raise ValueError("strategy_name is required")
        if not self.strategy_version:
            raise ValueError("strategy_version is required")
        if self.target_horizon_minutes <= 0:
            raise ValueError("target_horizon_minutes must be positive")
        if self.fee_bps < 0.0:
            raise ValueError("fee_bps must be non-negative")
        if self.slippage_bps < 0.0:
            raise ValueError("slippage_bps must be non-negative")
        if self.min_prediction_confidence < 0.0 or self.min_prediction_confidence > 1.0:
            raise ValueError("min_prediction_confidence must be within [0, 1]")
        if self.min_rr <= 0.0:
            raise ValueError("min_rr must be positive")
