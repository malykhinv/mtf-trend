from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WalkForwardPredictionConfig:
    """Configuration for MVP1 walk-forward calibrated baseline prediction.

    This module intentionally avoids trade thresholds and PnL optimization. It
    only estimates OOS probabilities of descriptive future-nature scenarios.
    """

    prediction_version: str = "mvp1_weekly_walk_forward_calibrated_baseline_v1"
    target_horizon_minutes: int = 30
    purge_horizon_minutes: int = 60
    min_train_rows: int = 3
    min_group_rows: int = 2
    smoothing_strength: float = 5.0
    model_family: str = "state_bin_empirical_calibrated_baseline"

    def __post_init__(self) -> None:
        if self.target_horizon_minutes not in (15, 30, 60, 120):
            raise ValueError("target_horizon_minutes must be one of 15, 30, 60, or 120")
        if self.purge_horizon_minutes < self.target_horizon_minutes:
            raise ValueError("purge_horizon_minutes must be >= target_horizon_minutes")
        if self.min_train_rows <= 0:
            raise ValueError("min_train_rows must be positive")
        if self.min_group_rows <= 0:
            raise ValueError("min_group_rows must be positive")
        if self.smoothing_strength <= 0:
            raise ValueError("smoothing_strength must be positive")
        if not self.prediction_version:
            raise ValueError("prediction_version is required")
        if not self.model_family:
            raise ValueError("model_family is required")
