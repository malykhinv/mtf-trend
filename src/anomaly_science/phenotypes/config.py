from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from anomaly_science.runtime import DEFAULT_BOUNDED_CPU_THREAD_COUNT, validate_bounded_thread_count


class PhenotypeConfigError(ValueError):
    """Raised when a cross-fitted phenotype protocol is incomplete or unsafe."""


def _timestamp_ms(value: str, *, field_name: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PhenotypeConfigError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise PhenotypeConfigError(f"{field_name} must include timezone")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


@dataclass(frozen=True, slots=True)
class PhenotypeInputContract:
    group_column: str
    split_group_column: str
    symbol_column: str
    label_column: str
    snapshot_time_column: str
    feature_cutoff_time_column: str
    future_start_time_column: str
    resolution_time_column: str
    label_available_column: str
    label_schema_column: str
    required_label_schema_value: str
    row_filter_column: str | None = None
    row_filter_value: str | int | bool | None = None
    population_minimum_column: str | None = None
    population_minimum_value: float | None = None
    required_true_columns: tuple[str, ...] = ()
    label_only_columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        required = (
            self.group_column, self.split_group_column, self.symbol_column,
            self.label_column, self.snapshot_time_column,
            self.feature_cutoff_time_column, self.future_start_time_column,
            self.resolution_time_column, self.label_available_column,
            self.label_schema_column, self.required_label_schema_value,
        )
        if any(not value for value in required):
            raise PhenotypeConfigError("phenotype input contract fields are required")
        if (self.row_filter_column is None) != (self.row_filter_value is None):
            raise PhenotypeConfigError("row filter column/value must be set together")
        if (self.population_minimum_column is None) != (self.population_minimum_value is None):
            raise PhenotypeConfigError("population minimum column/value must be set together")
        for name, values in (
            ("required_true_columns", self.required_true_columns),
            ("label_only_columns", self.label_only_columns),
        ):
            if any(not value for value in values) or len(values) != len(set(values)):
                raise PhenotypeConfigError(f"{name} must contain unique non-empty names")


@dataclass(frozen=True, slots=True)
class PhenotypeGeneratorSpec:
    seed: int
    depth: int

    def __post_init__(self) -> None:
        if self.seed < 0 or not 2 <= self.depth <= 7:
            raise PhenotypeConfigError("generator seed/depth are invalid")


@dataclass(frozen=True, slots=True)
class CrossFittedPhenotypeConfig:
    protocol_freeze_id: str
    search_start_utc: str
    search_end_utc: str
    calibration_end_utc: str
    verification_end_utc: str
    input: PhenotypeInputContract
    numeric_features: tuple[str, ...]
    categorical_features: tuple[str, ...] = ()
    generators: tuple[PhenotypeGeneratorSpec, ...] = (
        PhenotypeGeneratorSpec(seed=20260701, depth=3),
        PhenotypeGeneratorSpec(seed=20260713, depth=4),
        PhenotypeGeneratorSpec(seed=20260729, depth=5),
    )
    cross_fit_folds: int = 3
    initial_train_fraction: float = 0.50
    iterations: int = 128
    learning_rate: float = 0.05
    l2_leaf_reg: float = 5.0
    catboost_thread_count: int = DEFAULT_BOUNDED_CPU_THREAD_COUNT
    min_fold_events: int = 30
    min_fold_fade_rate: float = 0.58
    min_fold_lift: float = 1.12
    min_fold_wilson_lower_95: float = 0.48
    consensus_membership_jaccard: float = 0.60
    min_fold_support_count: int = 2
    min_generator_support_count: int = 2
    max_frozen_phenotypes: int = 50
    max_frozen_membership_jaccard: float = 0.85
    calibration_min_events: int = 50
    beta_prior_strength: float = 20.0
    calibration_candidate_probability: float = 0.58
    high_probability_threshold: float = 0.70
    verification_min_events: int = 50
    verification_min_observed_rate: float = 0.65
    verification_min_wilson_lower_95: float = 0.60
    verification_max_calibration_gap: float = 0.08
    verification_min_positive_week_fraction: float = 0.60
    fdr_alpha: float = 0.05
    null_permutations: int = 19
    null_workers: int = 2
    random_seed: int = 20260701

    def __post_init__(self) -> None:
        boundaries = (
            self.search_start_ms,
            self.search_end_ms,
            self.calibration_end_ms,
            self.verification_end_ms,
        )
        if not boundaries[0] < boundaries[1] < boundaries[2] < boundaries[3]:
            raise PhenotypeConfigError(
                "periods must satisfy search_start < search_end < calibration_end < verification_end"
            )
        if not self.protocol_freeze_id.strip():
            raise PhenotypeConfigError("protocol_freeze_id is required")
        features = (*self.numeric_features, *self.categorical_features)
        if not features or len(features) != len(set(features)):
            raise PhenotypeConfigError("phenotype features must be non-empty and unique")
        forbidden = set(features) & set(self.input.label_only_columns)
        if forbidden:
            raise PhenotypeConfigError(f"label-only phenotype features are forbidden: {sorted(forbidden)}")
        if not 2 <= self.cross_fit_folds <= 6 or not 0.30 <= self.initial_train_fraction <= 0.75:
            raise PhenotypeConfigError("cross-fit folds/initial train fraction are invalid")
        if not self.generators or len(self.generators) != len(set(self.generators)):
            raise PhenotypeConfigError("generators must be non-empty and unique")
        if self.min_fold_support_count > self.cross_fit_folds:
            raise PhenotypeConfigError("fold support cannot exceed cross-fit folds")
        if self.min_generator_support_count > len(self.generators):
            raise PhenotypeConfigError("generator support cannot exceed generators")
        if self.iterations <= 0 or self.learning_rate <= 0 or self.l2_leaf_reg < 0:
            raise PhenotypeConfigError("CatBoost parameters are invalid")
        validate_bounded_thread_count(
            self.catboost_thread_count, field_name="catboost_thread_count"
        )
        for name in (
            "min_fold_fade_rate", "min_fold_wilson_lower_95",
            "consensus_membership_jaccard", "calibration_candidate_probability",
            "high_probability_threshold", "verification_min_observed_rate",
            "verification_min_wilson_lower_95",
            "verification_max_calibration_gap",
            "verification_min_positive_week_fraction", "fdr_alpha",
        ):
            value = getattr(self, name)
            if not 0.0 < value < 1.0:
                raise PhenotypeConfigError(f"{name} must lie in (0,1)")
        if self.min_fold_lift <= 1.0:
            raise PhenotypeConfigError("min_fold_lift must exceed one")
        if not 0.0 <= self.max_frozen_membership_jaccard < 1.0:
            raise PhenotypeConfigError("max frozen Jaccard must lie in [0,1)")
        if self.max_frozen_phenotypes <= 0 or self.calibration_min_events <= 0:
            raise PhenotypeConfigError("phenotype count/support must be positive")
        if self.verification_min_events <= 0 or self.min_fold_events <= 0:
            raise PhenotypeConfigError("verification/fold support must be positive")
        if self.beta_prior_strength <= 0:
            raise PhenotypeConfigError("beta prior strength must be positive")
        if self.null_permutations < 19:
            raise PhenotypeConfigError("at least 19 null permutations are required")
        if not 1 <= self.null_workers <= 4:
            raise PhenotypeConfigError("null_workers must be in 1..4")

    @property
    def search_start_ms(self) -> int:
        return _timestamp_ms(self.search_start_utc, field_name="search_start_utc")

    @property
    def search_end_ms(self) -> int:
        return _timestamp_ms(self.search_end_utc, field_name="search_end_utc")

    @property
    def calibration_end_ms(self) -> int:
        return _timestamp_ms(self.calibration_end_utc, field_name="calibration_end_utc")

    @property
    def verification_end_ms(self) -> int:
        return _timestamp_ms(self.verification_end_utc, field_name="verification_end_utc")


def cross_fitted_phenotype_config_from_mapping(
    payload: dict[str, Any],
) -> CrossFittedPhenotypeConfig:
    raw = dict(payload)
    input_raw = dict(raw.pop("input"))
    for name in ("required_true_columns", "label_only_columns"):
        if name in input_raw:
            input_raw[name] = tuple(input_raw[name])
    generators = tuple(
        PhenotypeGeneratorSpec(**item) for item in raw.pop("generators", [])
    )
    for name in ("numeric_features", "categorical_features"):
        if name in raw:
            raw[name] = tuple(raw[name])
    unknown = sorted(set(raw) - set(CrossFittedPhenotypeConfig.__dataclass_fields__))
    if unknown:
        raise PhenotypeConfigError(f"unknown phenotype config fields: {unknown}")
    if generators:
        raw["generators"] = generators
    return CrossFittedPhenotypeConfig(
        input=PhenotypeInputContract(**input_raw),
        **raw,
    )


def load_cross_fitted_phenotype_config(path: Path) -> CrossFittedPhenotypeConfig:
    return cross_fitted_phenotype_config_from_mapping(
        json.loads(path.read_text(encoding="utf-8"))
    )


__all__ = [
    "CrossFittedPhenotypeConfig",
    "PhenotypeConfigError",
    "PhenotypeGeneratorSpec",
    "PhenotypeInputContract",
    "cross_fitted_phenotype_config_from_mapping",
    "load_cross_fitted_phenotype_config",
]
