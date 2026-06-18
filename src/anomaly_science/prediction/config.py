from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WalkForwardPredictionConfig:
    """Configuration for MVP1 weekly CatBoost+Isotonic prediction.

    This module intentionally avoids trade thresholds and PnL optimization. It
    only estimates OOS probabilities of descriptive future-nature scenarios.
    """

    prediction_version: str = "mvp1_weekly_walk_forward_catboost_isotonic_v1"
    strategy_version: str = "broad_anomaly_v1_h30"
    target_horizon_minutes: int = 30
    purge_horizon_minutes: int = 60
    min_train_rows: int = 80
    min_group_rows: int = 2
    smoothing_strength: float = 5.0
    model_family: str = "catboost_isotonic_weekly"
    catboost_iterations: int = 80
    catboost_depth: int = 4
    catboost_learning_rate: float = 0.05
    random_seed: int = 20260618
    excluded_model_feature_prefixes: tuple[str, ...] = ()

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
        if self.catboost_iterations <= 0:
            raise ValueError("catboost_iterations must be positive")
        if self.catboost_depth <= 0:
            raise ValueError("catboost_depth must be positive")
        if self.catboost_learning_rate <= 0.0:
            raise ValueError("catboost_learning_rate must be positive")
        if not self.prediction_version:
            raise ValueError("prediction_version is required")
        if not self.strategy_version:
            raise ValueError("strategy_version is required")
        if not self.model_family:
            raise ValueError("model_family is required")
        if any(not item for item in self.excluded_model_feature_prefixes):
            raise ValueError("excluded_model_feature_prefixes must not contain empty values")
