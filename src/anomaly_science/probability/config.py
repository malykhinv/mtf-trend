from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


class BinaryProbabilityConfigError(ValueError):
    """Raised when a binary weekly walk-forward protocol is incomplete."""


DIRECT_ISOTONIC_V1 = "direct_isotonic_v1"
QUANTILE_BINNED_BETA_ISOTONIC_V2 = "quantile_binned_beta_isotonic_v2"
SUPPORTED_CALIBRATION_METHODS = (
    DIRECT_ISOTONIC_V1,
    QUANTILE_BINNED_BETA_ISOTONIC_V2,
)


@dataclass(frozen=True, slots=True)
class BinaryProbabilityGates:
    min_oos_rows: int = 1_000
    max_skipped_oos_week_fraction: float = 0.0
    min_auc: float = 0.60
    min_log_loss_improvement: float = 0.005
    min_brier_improvement: float = 0.002
    max_ece: float = 0.05
    max_auc_permutation_p_value: float = 0.05
    max_log_loss_permutation_p_value: float = 0.05
    high_probability_threshold: float = 0.70
    min_high_probability_rows: int = 100
    min_high_probability_observed_rate: float = 0.65
    min_high_probability_wilson_lower_95: float = 0.60
    max_high_probability_calibration_gap: float = 0.08

    def __post_init__(self) -> None:
        if self.min_oos_rows <= 0 or self.min_high_probability_rows <= 0:
            raise BinaryProbabilityConfigError("probability gate row counts must be positive")
        for name in (
            "min_auc",
            "max_ece",
            "high_probability_threshold",
            "min_high_probability_observed_rate",
            "min_high_probability_wilson_lower_95",
            "max_high_probability_calibration_gap",
            "max_skipped_oos_week_fraction",
            "max_auc_permutation_p_value",
            "max_log_loss_permutation_p_value",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise BinaryProbabilityConfigError(f"{name} must lie in [0, 1]")
        if self.min_log_loss_improvement < 0.0 or self.min_brier_improvement < 0.0:
            raise BinaryProbabilityConfigError("improvement gates must be non-negative")


@dataclass(frozen=True, slots=True)
class BinaryWeeklyWalkForwardConfig:
    protocol_freeze_id: str
    strategy_name: str
    target_name: str
    development_start_ms: int
    oos_start_ms: int
    oos_end_ms: int
    numeric_features: tuple[str, ...]
    categorical_features: tuple[str, ...] = ()
    breakdown_columns: tuple[str, ...] = ()
    required_true_columns: tuple[str, ...] = ()
    required_finite_columns: tuple[str, ...] = ()
    group_column: str = "group"
    split_group_column: str = ""
    symbol_column: str = "symbol"
    snapshot_time_column: str = "snapshot_time_ms"
    feature_cutoff_time_column: str = "feature_cutoff_time_ms"
    future_start_time_column: str = "nature_future_start_time_ms"
    resolution_time_column: str = "nature_resolution_time_ms"
    label_column: str = "nature_y"
    label_available_column: str = "nature_label_available"
    row_filter_column: str = "is_nature_anchor"
    row_filter_value: bool = True
    population_minimum_column: str = ""
    population_minimum_value: float | None = None
    population_exact_column: str = ""
    population_exact_value: str | int | float | bool | None = None
    required_label_schema_column: str = "nature_label_schema_version"
    required_label_schema_value: str = "pump_fade_event_peak_close_race_v1"
    required_input_schema_column: str = ""
    required_input_schema_value: str = ""
    label_only_columns: tuple[str, ...] = ()
    min_train_rows: int = 1_000
    min_split_rows: int = 100
    min_class_rows_per_split: int = 10
    fit_fraction: float = 0.60
    validation_fraction: float = 0.20
    catboost_iterations: int = 200
    catboost_depth: int = 5
    catboost_learning_rate: float = 0.05
    catboost_l2_leaf_reg: float = 5.0
    catboost_early_stopping_rounds: int = 30
    catboost_thread_count: int = 4
    random_seed: int = 20260629
    calibration_bins: int = 10
    calibration_method: str = DIRECT_ISOTONIC_V1
    isotonic_fit_bins: int = 20
    isotonic_beta_prior_strength: float = 20.0
    reliability_thresholds: tuple[float, ...] = (0.50, 0.60, 0.70, 0.80, 0.90)
    null_permutations: int = 999
    gates: BinaryProbabilityGates = BinaryProbabilityGates()

    def __post_init__(self) -> None:
        for name in ("protocol_freeze_id", "strategy_name", "target_name"):
            if not getattr(self, name):
                raise BinaryProbabilityConfigError(f"{name} is required")
        if not (0 <= self.development_start_ms < self.oos_start_ms < self.oos_end_ms):
            raise BinaryProbabilityConfigError(
                "development_start_ms, oos_start_ms, and oos_end_ms must increase strictly"
            )
        if not self.numeric_features and not self.categorical_features:
            raise BinaryProbabilityConfigError("at least one model feature is required")
        features = (*self.numeric_features, *self.categorical_features)
        if len(features) != len(set(features)):
            raise BinaryProbabilityConfigError("model feature names must be unique")
        if len(self.breakdown_columns) != len(set(self.breakdown_columns)):
            raise BinaryProbabilityConfigError("breakdown_columns must be unique")
        if len(self.required_true_columns) != len(set(self.required_true_columns)):
            raise BinaryProbabilityConfigError("required_true_columns must be unique")
        if len(self.required_finite_columns) != len(set(self.required_finite_columns)):
            raise BinaryProbabilityConfigError("required_finite_columns must be unique")
        overlap = sorted(set(features) & set(self.label_only_columns))
        if overlap:
            raise BinaryProbabilityConfigError(
                f"label-only columns cannot be model features: {overlap}"
            )
        required_names = (
            self.group_column,
            self.symbol_column,
            self.snapshot_time_column,
            self.feature_cutoff_time_column,
            self.future_start_time_column,
            self.resolution_time_column,
            self.label_column,
            self.label_available_column,
            self.row_filter_column,
            self.required_label_schema_column,
        )
        if any(not name for name in required_names):
            raise BinaryProbabilityConfigError("input contract column names are required")
        if bool(self.population_minimum_column) != (self.population_minimum_value is not None):
            raise BinaryProbabilityConfigError(
                "population_minimum_column and population_minimum_value must be set together"
            )
        if bool(self.population_exact_column) != (self.population_exact_value is not None):
            raise BinaryProbabilityConfigError(
                "population_exact_column and population_exact_value must be set together"
            )
        if bool(self.required_input_schema_column) != bool(self.required_input_schema_value):
            raise BinaryProbabilityConfigError(
                "required_input_schema_column and required_input_schema_value must be set together"
            )
        if self.min_train_rows <= 0 or self.min_split_rows <= 0:
            raise BinaryProbabilityConfigError("minimum row counts must be positive")
        if self.min_class_rows_per_split <= 0:
            raise BinaryProbabilityConfigError("min_class_rows_per_split must be positive")
        if not 0.0 < self.fit_fraction < 1.0 or not 0.0 < self.validation_fraction < 1.0:
            raise BinaryProbabilityConfigError("split fractions must lie in (0, 1)")
        if self.fit_fraction + self.validation_fraction >= 1.0:
            raise BinaryProbabilityConfigError("fit and validation fractions must leave calibration rows")
        for name in (
            "catboost_iterations",
            "catboost_depth",
            "catboost_early_stopping_rounds",
            "catboost_thread_count",
            "calibration_bins",
            "null_permutations",
        ):
            if int(getattr(self, name)) <= 0:
                raise BinaryProbabilityConfigError(f"{name} must be positive")
        if self.catboost_thread_count > 16:
            raise BinaryProbabilityConfigError("catboost_thread_count must not exceed 16")
        if self.catboost_learning_rate <= 0.0 or self.catboost_l2_leaf_reg < 0.0:
            raise BinaryProbabilityConfigError("CatBoost learning rate/L2 settings are invalid")
        if self.calibration_bins < 2:
            raise BinaryProbabilityConfigError("calibration_bins must be at least two")
        if self.calibration_method not in SUPPORTED_CALIBRATION_METHODS:
            raise BinaryProbabilityConfigError(
                f"calibration_method must be one of {SUPPORTED_CALIBRATION_METHODS!r}"
            )
        if self.isotonic_fit_bins < 2:
            raise BinaryProbabilityConfigError("isotonic_fit_bins must be at least two")
        if self.isotonic_beta_prior_strength <= 0.0:
            raise BinaryProbabilityConfigError(
                "isotonic_beta_prior_strength must be positive"
            )
        if not self.reliability_thresholds:
            raise BinaryProbabilityConfigError("reliability_thresholds must not be empty")
        if tuple(sorted(set(self.reliability_thresholds))) != self.reliability_thresholds:
            raise BinaryProbabilityConfigError("reliability_thresholds must be unique and sorted")
        if any(not 0.0 < value < 1.0 for value in self.reliability_thresholds):
            raise BinaryProbabilityConfigError("reliability thresholds must lie in (0, 1)")


def load_binary_weekly_walk_forward_config(path: Path) -> BinaryWeeklyWalkForwardConfig:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    time_values: dict[str, int] = {}
    for prefix in ("development_start", "oos_start", "oos_end"):
        utc_key = f"{prefix}_utc"
        ms_key = f"{prefix}_ms"
        if utc_key in raw and ms_key in raw:
            raise BinaryProbabilityConfigError(f"use only one of {utc_key} and {ms_key}")
        if utc_key in raw:
            time_values[ms_key] = _parse_utc_ms(raw.pop(utc_key), field_name=utc_key)
        elif ms_key in raw:
            time_values[ms_key] = int(raw.pop(ms_key))
        else:
            raise BinaryProbabilityConfigError(f"{utc_key} or {ms_key} is required")
    for name in (
        "numeric_features",
        "categorical_features",
        "breakdown_columns",
        "required_true_columns",
        "required_finite_columns",
        "label_only_columns",
        "reliability_thresholds",
    ):
        if name in raw:
            raw[name] = tuple(raw[name])
    if "gates" in raw:
        if not isinstance(raw["gates"], dict):
            raise BinaryProbabilityConfigError("gates must be a JSON object")
        unknown_gates = sorted(set(raw["gates"]) - set(BinaryProbabilityGates.__dataclass_fields__))
        if unknown_gates:
            raise BinaryProbabilityConfigError(f"unknown gate fields: {unknown_gates}")
        raw["gates"] = BinaryProbabilityGates(**raw["gates"])
    unknown = sorted(set(raw) - set(BinaryWeeklyWalkForwardConfig.__dataclass_fields__))
    if unknown:
        raise BinaryProbabilityConfigError(f"unknown binary probability config fields: {unknown}")
    return BinaryWeeklyWalkForwardConfig(**time_values, **raw)


def _parse_utc_ms(value: object, *, field_name: str) -> int:
    if not isinstance(value, str) or not value:
        raise BinaryProbabilityConfigError(f"{field_name} must be a non-empty ISO UTC string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise BinaryProbabilityConfigError(f"{field_name} must include a timezone")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


__all__ = [
    "DIRECT_ISOTONIC_V1",
    "QUANTILE_BINNED_BETA_ISOTONIC_V2",
    "SUPPORTED_CALIBRATION_METHODS",
    "BinaryProbabilityConfigError",
    "BinaryProbabilityGates",
    "BinaryWeeklyWalkForwardConfig",
    "load_binary_weekly_walk_forward_config",
]
