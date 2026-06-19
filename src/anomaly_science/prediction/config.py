from __future__ import annotations

from dataclasses import dataclass

from anomaly_science.contracts.horizons import research_horizon_label_column
from anomaly_science.strategy.metadata import active_strategy_h_max_minutes
from anomaly_science.strategy.registry import validate_strategy_horizon


@dataclass(frozen=True, slots=True)
class WalkForwardPredictionConfig:
    """Configuration for MVP1 weekly CatBoost+Isotonic prediction.

    This module intentionally avoids trade thresholds and PnL optimization. It
    only estimates OOS probabilities of descriptive future-nature scenarios.
    """

    prediction_version: str = "mvp1_weekly_walk_forward_catboost_isotonic_v1"
    strategy_name: str = "broad_anomaly_v1_h30"
    target_horizon_minutes: int = 30
    active_strategy_names: tuple[str, ...] = ()
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
        if not self.prediction_version:
            raise ValueError("prediction_version is required")
        if not self.strategy_name:
            raise ValueError("strategy_name is required")
        if not self.model_family:
            raise ValueError("model_family is required")
        if not self.active_strategy_names:
            object.__setattr__(self, "active_strategy_names", (self.strategy_name,))
        if self.strategy_name not in self.active_strategy_names:
            raise ValueError("active_strategy_names must include strategy_name")
        validate_strategy_horizon(self.strategy_name, self.target_horizon_minutes)
        if self.purge_horizon_minutes < self.target_horizon_minutes:
            raise ValueError("active strategy H_max must be >= target_horizon_minutes")
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
        if any(not item for item in self.excluded_model_feature_prefixes):
            raise ValueError("excluded_model_feature_prefixes must not contain empty values")

    @property
    def active_h_max_minutes(self) -> int:
        return active_strategy_h_max_minutes(self.active_strategy_names)

    @property
    def purge_horizon_minutes(self) -> int:
        return self.active_h_max_minutes

    @property
    def target_label_column(self) -> str:
        return research_horizon_label_column(self.target_horizon_minutes)
