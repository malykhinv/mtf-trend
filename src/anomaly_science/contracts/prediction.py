from __future__ import annotations

from dataclasses import dataclass

from .labels import PREDICTABLE_OUTCOME_SCENARIOS, VALID_OUTCOME_SCENARIOS
from .market import MarketDataContractError
from .time import validate_timestamp_ms

PREDICTED_SCENARIOS = PREDICTABLE_OUTCOME_SCENARIOS
PREDICTION_TEMPORAL_CONTRACT = "train_snapshot_time_ms_plus_horizon<=weekly_model_freeze_time_ms;features<=snapshot_time<label_future_start"


@dataclass(frozen=True, slots=True)
class OosPredictionRow:
    prediction_version: str
    event_id: str
    symbol: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    future_start_time_ms: int
    target_horizon_minutes: int
    target_scenario: str
    test_day: str
    train_cutoff_time_ms: int
    model_family: str
    model_key: str
    model_train_row_count: int
    model_group_row_count: int
    p_long_continuation: float
    p_short_fade: float
    p_static_or_chop: float
    p_unclear: float
    predicted_scenario: str
    prediction_confidence: float
    temporal_contract: str

    def __post_init__(self) -> None:
        if not self.prediction_version:
            raise MarketDataContractError("prediction_version is required")
        if not self.event_id:
            raise MarketDataContractError("event_id is required")
        if not self.symbol:
            raise MarketDataContractError("symbol is required")
        validate_timestamp_ms(self.snapshot_time_ms, field_name="snapshot_time_ms")
        validate_timestamp_ms(self.feature_cutoff_time_ms, field_name="feature_cutoff_time_ms")
        validate_timestamp_ms(self.future_start_time_ms, field_name="future_start_time_ms")
        validate_timestamp_ms(self.train_cutoff_time_ms, field_name="train_cutoff_time_ms")
        if self.feature_cutoff_time_ms > self.snapshot_time_ms:
            raise MarketDataContractError("prediction feature_cutoff_time_ms must be <= snapshot_time_ms")
        if self.future_start_time_ms <= self.snapshot_time_ms:
            raise MarketDataContractError("prediction future_start_time_ms must be > snapshot_time_ms")
        if self.target_horizon_minutes <= 0:
            raise MarketDataContractError("target_horizon_minutes must be positive")
        if self.target_scenario not in PREDICTED_SCENARIOS:
            raise MarketDataContractError(f"target_scenario is not predictable in MVP1: {self.target_scenario!r}")
        if self.predicted_scenario not in PREDICTED_SCENARIOS:
            raise MarketDataContractError(f"predicted_scenario has unknown value: {self.predicted_scenario!r}")
        if self.model_train_row_count <= 0:
            raise MarketDataContractError("model_train_row_count must be positive")
        if self.model_group_row_count < 0:
            raise MarketDataContractError("model_group_row_count must be non-negative")
        probabilities = (
            self.p_long_continuation,
            self.p_short_fade,
            self.p_static_or_chop,
            self.p_unclear,
        )
        for value in probabilities:
            if value < 0.0 or value > 1.0:
                raise MarketDataContractError("prediction probabilities must be within [0, 1]")
        if abs(sum(probabilities) - 1.0) > 1e-9:
            raise MarketDataContractError("prediction probabilities must sum to 1")
        if abs(max(probabilities) - self.prediction_confidence) > 1e-9:
            raise MarketDataContractError("prediction_confidence must equal max class probability")
        if self.temporal_contract != PREDICTION_TEMPORAL_CONTRACT:
            raise MarketDataContractError("temporal_contract must document the walk-forward prediction boundary")
        if self.target_scenario not in VALID_OUTCOME_SCENARIOS:
            raise MarketDataContractError("target_scenario must be a known outcome scenario")


@dataclass(frozen=True, slots=True)
class CalibrationRow:
    prediction_version: str
    target_horizon_minutes: int
    predicted_scenario: str
    confidence_bucket: str
    row_count: int
    mean_confidence: float
    empirical_accuracy: float
    multiclass_brier: float
    log_loss: float

    def __post_init__(self) -> None:
        if not self.prediction_version:
            raise MarketDataContractError("prediction_version is required")
        if self.target_horizon_minutes <= 0:
            raise MarketDataContractError("target_horizon_minutes must be positive")
        if self.predicted_scenario not in PREDICTED_SCENARIOS:
            raise MarketDataContractError("predicted_scenario has unknown value")
        if not self.confidence_bucket:
            raise MarketDataContractError("confidence_bucket is required")
        if self.row_count <= 0:
            raise MarketDataContractError("row_count must be positive")
        for field_name in ("mean_confidence", "empirical_accuracy", "multiclass_brier", "log_loss"):
            value = getattr(self, field_name)
            if value < 0.0:
                raise MarketDataContractError(f"{field_name} must be non-negative")


@dataclass(frozen=True, slots=True)
class PredictionMetricRow:
    prediction_version: str
    target_horizon_minutes: int
    metric_name: str
    metric_value: str
    row_count: int
    notes: str

    def __post_init__(self) -> None:
        if not self.prediction_version:
            raise MarketDataContractError("prediction_version is required")
        if self.target_horizon_minutes <= 0:
            raise MarketDataContractError("target_horizon_minutes must be positive")
        if not self.metric_name:
            raise MarketDataContractError("metric_name is required")
        if self.row_count < 0:
            raise MarketDataContractError("row_count must be non-negative")
