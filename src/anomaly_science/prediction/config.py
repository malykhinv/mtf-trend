from __future__ import annotations

from dataclasses import dataclass
from anomaly_science.contracts.horizons import research_horizon_label_column
from anomaly_science.strategy.defaults import DEFAULT_RESEARCH_STRATEGY_NAME
from anomaly_science.strategy.metadata import active_strategy_h_max_minutes
from anomaly_science.strategy.registry import validate_strategy_horizon
from anomaly_science.runtime import DEFAULT_BOUNDED_CPU_THREAD_COUNT, validate_bounded_thread_count


SUPERVISED_ANCHOR_POLICY_T0_ONLY = "t0_only_v1"
SUPERVISED_ANCHOR_POLICY_REGISTERED_STATE_LATTICE = "registered_state_lattice_v1"
REGISTERED_STATE_LATTICE_ANCHORS_MINUTES = (0, 5, 10, 15, 30, 60)
SUPPORTED_SUPERVISED_ANCHOR_POLICIES = (
    SUPERVISED_ANCHOR_POLICY_T0_ONLY,
    SUPERVISED_ANCHOR_POLICY_REGISTERED_STATE_LATTICE,
)
SAMPLE_WEIGHT_POLICY_EVENT_ANCHOR_NORMALIZED = "event_anchor_normalized_v1"
SUPPORTED_SAMPLE_WEIGHT_POLICIES = ("uniform_v1", SAMPLE_WEIGHT_POLICY_EVENT_ANCHOR_NORMALIZED)


@dataclass(frozen=True, slots=True)
class WalkForwardPredictionConfig:
    """Configuration for MVP1 weekly CatBoost+Isotonic prediction.

    This module intentionally avoids trade thresholds and PnL optimization. It
    only estimates OOS probabilities of descriptive future-nature scenarios.
    """

    prediction_version: str = "mvp1_weekly_walk_forward_catboost_isotonic_v1"
    strategy_name: str = DEFAULT_RESEARCH_STRATEGY_NAME
    target_horizon_minutes: int = 30
    active_strategy_names: tuple[str, ...] = ()
    min_train_rows: int = 80
    min_group_rows: int = 2
    smoothing_strength: float = 5.0
    model_family: str = "catboost_isotonic_weekly"
    catboost_iterations: int = 80
    catboost_depth: int = 4
    catboost_learning_rate: float = 0.05
    catboost_thread_count: int = DEFAULT_BOUNDED_CPU_THREAD_COUNT
    random_seed: int = 20260618
    excluded_model_feature_prefixes: tuple[str, ...] = ()
    sample_weight_policy: str = SAMPLE_WEIGHT_POLICY_EVENT_ANCHOR_NORMALIZED
    # Pre-registered supervised anchor policy. The default bounded lattice keeps
    # a small set of causal post-detection state snapshots instead of pretending
    # the detection-minute anchor is the only scientific question. It is still
    # bounded for i5/16GB runs and avoids full per-minute pseudo-replication.
    supervised_anchor_policy_id: str = SUPERVISED_ANCHOR_POLICY_REGISTERED_STATE_LATTICE
    supervised_anchor_minutes_since_detection: int = 0
    supervised_anchor_offsets_minutes_since_detection: tuple[int, ...] = REGISTERED_STATE_LATTICE_ANCHORS_MINUTES

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
        validate_bounded_thread_count(self.catboost_thread_count, field_name="catboost_thread_count")
        if any(not item for item in self.excluded_model_feature_prefixes):
            raise ValueError("excluded_model_feature_prefixes must not contain empty values")
        if self.sample_weight_policy not in SUPPORTED_SAMPLE_WEIGHT_POLICIES:
            raise ValueError(f"sample_weight_policy must be one of {SUPPORTED_SAMPLE_WEIGHT_POLICIES!r}")
        if self.supervised_anchor_policy_id not in SUPPORTED_SUPERVISED_ANCHOR_POLICIES:
            raise ValueError(f"supervised_anchor_policy_id must be one of {SUPPORTED_SUPERVISED_ANCHOR_POLICIES!r}")
        if self.supervised_anchor_minutes_since_detection < 0:
            raise ValueError("supervised_anchor_minutes_since_detection must be non-negative")
        offsets = _normalize_anchor_offsets(self.supervised_anchor_offsets_minutes_since_detection)
        if self.supervised_anchor_policy_id == SUPERVISED_ANCHOR_POLICY_REGISTERED_STATE_LATTICE:
            object.__setattr__(self, "supervised_anchor_offsets_minutes_since_detection", offsets)
        else:
            object.__setattr__(
                self,
                "supervised_anchor_offsets_minutes_since_detection",
                (self.supervised_anchor_minutes_since_detection,),
            )

    @property
    def active_h_max_minutes(self) -> int:
        return active_strategy_h_max_minutes(self.active_strategy_names)

    @property
    def purge_horizon_minutes(self) -> int:
        return self.active_h_max_minutes

    @property
    def target_label_column(self) -> str:
        return research_horizon_label_column(self.target_horizon_minutes)


def _normalize_anchor_offsets(offsets: tuple[int, ...]) -> tuple[int, ...]:
    if not offsets:
        raise ValueError("supervised_anchor_offsets_minutes_since_detection must not be empty")
    normalized = tuple(int(value) for value in offsets)
    if any(value < 0 for value in normalized):
        raise ValueError("supervised_anchor_offsets_minutes_since_detection must be non-negative")
    if len(set(normalized)) != len(normalized):
        raise ValueError("supervised_anchor_offsets_minutes_since_detection must be unique")
    if normalized != tuple(sorted(normalized)):
        raise ValueError("supervised_anchor_offsets_minutes_since_detection must be sorted")
    return normalized
