from __future__ import annotations

from dataclasses import dataclass

from .horizons import research_horizon_label_column, validate_supported_research_horizon
from .labels import PREDICTABLE_OUTCOME_SCENARIOS, VALID_OUTCOME_SCENARIOS
from .market import MarketDataContractError
from .time import validate_timestamp_ms

PREDICTED_SCENARIOS = PREDICTABLE_OUTCOME_SCENARIOS
PREDICTION_TEMPORAL_CONTRACT = "train_snapshot_time_ms_plus_horizon<=weekly_model_freeze_time_ms;features<=snapshot_time<label_future_start"


@dataclass(frozen=True, slots=True)
class OosPredictionRow:
    prediction_version: str
    strategy_name: str
    strategy_version: str
    strategy_contract_version: str
    target_label_column: str
    active_h_max_minutes: int
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
    raw_p_long_continuation: float
    raw_p_short_fade: float
    raw_p_static_or_chop: float
    raw_p_unclear: float
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
        if not self.strategy_name:
            raise MarketDataContractError("strategy_name is required")
        if not self.strategy_version:
            raise MarketDataContractError("strategy_version is required")
        if not self.strategy_contract_version:
            raise MarketDataContractError("strategy_contract_version is required")
        if self.active_h_max_minutes <= 0:
            raise MarketDataContractError("active_h_max_minutes must be positive")
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
        try:
            validate_supported_research_horizon(self.target_horizon_minutes, field_name="target_horizon_minutes")
        except ValueError as exc:
            raise MarketDataContractError(str(exc)) from exc
        if self.target_label_column != research_horizon_label_column(self.target_horizon_minutes):
            raise MarketDataContractError("target_label_column must match target_horizon_minutes")
        if self.active_h_max_minutes < self.target_horizon_minutes:
            raise MarketDataContractError("active_h_max_minutes must be >= target_horizon_minutes")
        if self.target_scenario not in PREDICTED_SCENARIOS:
            raise MarketDataContractError(f"target_scenario is not predictable in MVP1: {self.target_scenario!r}")
        if self.predicted_scenario not in PREDICTED_SCENARIOS:
            raise MarketDataContractError(f"predicted_scenario has unknown value: {self.predicted_scenario!r}")
        if self.model_train_row_count <= 0:
            raise MarketDataContractError("model_train_row_count must be positive")
        if self.model_group_row_count < 0:
            raise MarketDataContractError("model_group_row_count must be non-negative")
        raw_probabilities = (
            self.raw_p_long_continuation,
            self.raw_p_short_fade,
            self.raw_p_static_or_chop,
            self.raw_p_unclear,
        )
        probabilities = (
            self.p_long_continuation,
            self.p_short_fade,
            self.p_static_or_chop,
            self.p_unclear,
        )
        for value in (*raw_probabilities, *probabilities):
            if value < 0.0 or value > 1.0:
                raise MarketDataContractError("prediction probabilities must be within [0, 1]")
        if abs(sum(raw_probabilities) - 1.0) > 1e-9:
            raise MarketDataContractError("raw prediction probabilities must sum to 1")
        if abs(sum(probabilities) - 1.0) > 1e-9:
            raise MarketDataContractError("calibrated prediction probabilities must sum to 1")
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
        try:
            validate_supported_research_horizon(self.target_horizon_minutes, field_name="target_horizon_minutes")
        except ValueError as exc:
            raise MarketDataContractError(str(exc)) from exc
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
        try:
            validate_supported_research_horizon(self.target_horizon_minutes, field_name="target_horizon_minutes")
        except ValueError as exc:
            raise MarketDataContractError(str(exc)) from exc
        if not self.metric_name:
            raise MarketDataContractError("metric_name is required")
        if self.row_count < 0:
            raise MarketDataContractError("row_count must be non-negative")


@dataclass(frozen=True, slots=True)
class ModelMetadataRow:
    prediction_version: str
    model_key: str
    model_family: str
    strategy_name: str
    strategy_version: str
    strategy_contract_version: str
    target_horizon_minutes: int
    target_label_column: str
    active_h_max_minutes: int
    weekly_model_freeze_time_ms: int
    train_cutoff_time_ms: int
    train_row_count: int
    fit_row_count: int
    validation_row_count: int
    calibration_row_count: int
    best_iteration: int
    class_order: str
    model_feature_names: str
    calibration_method: str

    def __post_init__(self) -> None:
        if not self.prediction_version:
            raise MarketDataContractError("prediction_version is required")
        if not self.model_key:
            raise MarketDataContractError("model_key is required")
        if not self.model_family:
            raise MarketDataContractError("model_family is required")
        if not self.strategy_name:
            raise MarketDataContractError("strategy_name is required")
        if not self.strategy_version:
            raise MarketDataContractError("strategy_version is required")
        if not self.strategy_contract_version:
            raise MarketDataContractError("strategy_contract_version is required")
        try:
            validate_supported_research_horizon(self.target_horizon_minutes, field_name="target_horizon_minutes")
        except ValueError as exc:
            raise MarketDataContractError(str(exc)) from exc
        if self.target_label_column != research_horizon_label_column(self.target_horizon_minutes):
            raise MarketDataContractError("target_label_column must match target_horizon_minutes")
        if self.active_h_max_minutes < self.target_horizon_minutes:
            raise MarketDataContractError("active_h_max_minutes must be >= target_horizon_minutes")
        validate_timestamp_ms(self.weekly_model_freeze_time_ms, field_name="weekly_model_freeze_time_ms")
        validate_timestamp_ms(self.train_cutoff_time_ms, field_name="train_cutoff_time_ms")
        for field_name in ("train_row_count", "fit_row_count", "validation_row_count", "calibration_row_count"):
            if getattr(self, field_name) <= 0:
                raise MarketDataContractError(f"{field_name} must be positive")
        if self.best_iteration < 0:
            raise MarketDataContractError("best_iteration must be non-negative")
        if not self.class_order:
            raise MarketDataContractError("class_order is required")
        if not self.model_feature_names:
            raise MarketDataContractError("model_feature_names is required")
        if not self.calibration_method:
            raise MarketDataContractError("calibration_method is required")


@dataclass(frozen=True, slots=True)
class ModelTrainingDiagnosticRow:
    prediction_version: str
    model_key: str
    test_week: str
    weekly_model_freeze_time_ms: int
    train_cutoff_time_ms: int
    train_row_count: int
    fit_row_count: int
    validation_row_count: int
    calibration_row_count: int
    fit_class_count: int
    validation_class_count: int
    calibration_class_count: int
    status: str
    reason: str

    def __post_init__(self) -> None:
        if not self.prediction_version:
            raise MarketDataContractError("prediction_version is required")
        if not self.model_key:
            raise MarketDataContractError("model_key is required")
        if not self.test_week:
            raise MarketDataContractError("test_week is required")
        validate_timestamp_ms(self.weekly_model_freeze_time_ms, field_name="weekly_model_freeze_time_ms")
        validate_timestamp_ms(self.train_cutoff_time_ms, field_name="train_cutoff_time_ms")
        for field_name in (
            "train_row_count",
            "fit_row_count",
            "validation_row_count",
            "calibration_row_count",
            "fit_class_count",
            "validation_class_count",
            "calibration_class_count",
        ):
            if getattr(self, field_name) < 0:
                raise MarketDataContractError(f"{field_name} must be non-negative")
        if self.status not in {"TRAINED", "SKIPPED"}:
            raise MarketDataContractError("model training diagnostic status must be TRAINED or SKIPPED")
        if not self.reason:
            raise MarketDataContractError("model training diagnostic reason is required")


@dataclass(frozen=True, slots=True)
class FeatureImportanceRow:
    prediction_version: str
    model_key: str
    feature_name: str
    feature_importance: float
    rank: int

    def __post_init__(self) -> None:
        if not self.prediction_version:
            raise MarketDataContractError("prediction_version is required")
        if not self.model_key:
            raise MarketDataContractError("model_key is required")
        if not self.feature_name:
            raise MarketDataContractError("feature_name is required")
        if self.feature_importance < 0.0:
            raise MarketDataContractError("feature_importance must be non-negative")
        if self.rank <= 0:
            raise MarketDataContractError("rank must be positive")
