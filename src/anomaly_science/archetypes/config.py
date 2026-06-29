from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ArchetypeConfigError(ValueError):
    """Raised when an archetype-discovery specification is incomplete or unsafe."""


SUPPORTED_TIME_FEATURES = frozenset(
    {"hour_utc", "minute_of_hour", "minutes_from_round_hour", "day_of_week_utc"}
)


def parse_utc_timestamp_ms(value: str, *, field_name: str) -> int:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ArchetypeConfigError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ArchetypeConfigError(f"{field_name} must include a timezone")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


@dataclass(frozen=True, slots=True)
class ArchetypeInputContract:
    group_column: str
    symbol_column: str
    label_column: str
    snapshot_time_column: str
    resolution_time_column: str
    label_available_column: str | None = None
    label_schema_column: str | None = None
    required_label_schema_value: str | None = None
    feature_cutoff_time_column: str | None = None
    future_start_time_column: str | None = None
    attest_feature_cutoff_equals_snapshot: bool = False
    future_start_offset_ms: int | None = None
    row_filter_column: str | None = None
    required_row_filter_value: str | int | bool | None = None

    def __post_init__(self) -> None:
        required = (
            self.group_column,
            self.symbol_column,
            self.label_column,
            self.snapshot_time_column,
            self.resolution_time_column,
        )
        if any(not value for value in required):
            raise ArchetypeConfigError("all required input column names must be non-empty")
        if self.feature_cutoff_time_column is None and not self.attest_feature_cutoff_equals_snapshot:
            raise ArchetypeConfigError(
                "feature_cutoff_time_column is required unless the config explicitly attests "
                "feature_cutoff_equals_snapshot"
            )
        if self.future_start_time_column is None:
            if self.future_start_offset_ms is None or self.future_start_offset_ms <= 0:
                raise ArchetypeConfigError(
                    "future_start_time_column or a positive future_start_offset_ms is required"
                )
        if (self.label_schema_column is None) != (self.required_label_schema_value is None):
            raise ArchetypeConfigError(
                "label_schema_column and required_label_schema_value must be set together"
            )
        if (self.row_filter_column is None) != (self.required_row_filter_value is None):
            raise ArchetypeConfigError(
                "row_filter_column and required_row_filter_value must be set together"
            )


@dataclass(frozen=True, slots=True)
class ArchetypeFeatureSpec:
    numeric: tuple[str, ...]
    decision_timing_numeric: tuple[str, ...] = ()
    categorical: tuple[str, ...] = ()
    derived_time: tuple[str, ...] = ()
    include_decision_timing: bool = False

    def __post_init__(self) -> None:
        all_columns = (*self.numeric, *self.decision_timing_numeric, *self.categorical)
        if not all_columns:
            raise ArchetypeConfigError("at least one explicit causal feature is required")
        if len(set(all_columns)) != len(all_columns):
            raise ArchetypeConfigError("numeric and categorical feature columns must be unique")
        unknown = set(self.derived_time) - SUPPORTED_TIME_FEATURES
        if unknown:
            raise ArchetypeConfigError(f"unknown derived_time features: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class ArchetypeRequiredValue:
    column: str
    value: str | int | float | bool

    def __post_init__(self) -> None:
        if not self.column:
            raise ArchetypeConfigError("population required-value column must be non-empty")
        if not isinstance(self.value, (str, int, float, bool)):
            raise ArchetypeConfigError("population required value must be a JSON scalar")


@dataclass(frozen=True, slots=True)
class ArchetypePopulationSpec:
    minimum_value_column: str | None = None
    minimum_value: float | None = None
    required_values: tuple[ArchetypeRequiredValue, ...] = ()
    required_finite_columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.minimum_value_column is None) != (self.minimum_value is None):
            raise ArchetypeConfigError(
                "minimum_value_column and minimum_value must either both be set or both be omitted"
            )
        columns = tuple(item.column for item in self.required_values)
        if len(columns) != len(set(columns)):
            raise ArchetypeConfigError("population required-value columns must be unique")
        if any(not column for column in self.required_finite_columns):
            raise ArchetypeConfigError("population required-finite columns must be non-empty")
        if len(self.required_finite_columns) != len(set(self.required_finite_columns)):
            raise ArchetypeConfigError("population required-finite columns must be unique")


@dataclass(frozen=True, slots=True)
class ArchetypeGeneratorSpec:
    random_seed: int
    depth: int

    def __post_init__(self) -> None:
        if self.random_seed < 0:
            raise ArchetypeConfigError("generator random_seed must be non-negative")
        if not 1 <= self.depth <= 8:
            raise ArchetypeConfigError("generator depth must be within [1, 8]")


@dataclass(frozen=True, slots=True)
class ArchetypeDiscoveryConfig:
    discovery_start_utc: str
    discovery_end_utc: str
    verification_start_utc: str
    verification_end_utc: str
    input: ArchetypeInputContract
    features: ArchetypeFeatureSpec
    population: ArchetypePopulationSpec = field(default_factory=ArchetypePopulationSpec)
    random_seed: int = 20260627
    iterations: int = 96
    depth: int = 4
    learning_rate: float = 0.05
    l2_leaf_reg: float = 5.0
    catboost_thread_count: int = 4
    generators: tuple[ArchetypeGeneratorSpec, ...] = ()
    rolling_origin_fractions: tuple[float, ...] = (0.60, 0.80, 1.0)
    min_origin_support_fraction: float = 0.67
    min_generator_support_fraction: float = 0.50
    consensus_membership_jaccard: float = 0.70
    min_discovery_events: int = 80
    min_verification_events: int = 50
    min_discovery_fade_rate: float = 0.58
    min_verification_fade_rate: float = 0.58
    min_discovery_lift: float = 1.15
    min_verification_lift: float = 1.15
    max_membership_jaccard: float = 0.80
    min_unique_event_fraction: float = 0.25
    max_categories: int | None = None
    fdr_alpha: float = 0.05
    stability_frequency: str = "M"
    min_events_per_stability_period: int = 15
    min_positive_stability_fraction: float = 0.60
    matched_control_columns: tuple[str, ...] = ()
    matched_control_quantile_columns: tuple[str, ...] = ()
    matched_control_quantiles: int = 5
    min_matched_control_rows: int = 30
    min_matched_signal_fraction: float = 0.80
    block_bootstrap_iterations: int = 1000
    min_inference_blocks: int = 8
    evidence_mode: str = "development"
    protocol_freeze_id: str = ""
    shuffled_seeds: tuple[int, ...] = (
        11,
        29,
        47,
        61,
        79,
        97,
        113,
        131,
        149,
        167,
        181,
        199,
        223,
        241,
        263,
        281,
        307,
        331,
        353,
    )
    min_shuffled_row_fraction: float = 0.70
    control_empirical_alpha: float = 0.05
    threshold_audit_relative_step: float = 0.05
    threshold_audit_quantile_bins: int = 10
    threshold_audit_bootstrap_iterations: int = 200

    def __post_init__(self) -> None:
        ds = parse_utc_timestamp_ms(self.discovery_start_utc, field_name="discovery_start_utc")
        de = parse_utc_timestamp_ms(self.discovery_end_utc, field_name="discovery_end_utc")
        vs = parse_utc_timestamp_ms(self.verification_start_utc, field_name="verification_start_utc")
        ve = parse_utc_timestamp_ms(self.verification_end_utc, field_name="verification_end_utc")
        if not ds < de <= vs < ve:
            raise ArchetypeConfigError(
                "periods must satisfy discovery_start < discovery_end <= verification_start < verification_end"
            )
        if self.random_seed < 0 or any(seed < 0 for seed in self.shuffled_seeds):
            raise ArchetypeConfigError("random seeds must be non-negative")
        if not self.shuffled_seeds:
            raise ArchetypeConfigError("at least one shuffled seed is required")
        if not 0.0 < self.min_shuffled_row_fraction <= 1.0:
            raise ArchetypeConfigError("min_shuffled_row_fraction must be within (0, 1]")
        if not 0.0 < self.control_empirical_alpha < 1.0:
            raise ArchetypeConfigError("control_empirical_alpha must be within (0, 1)")
        if not 0.0 < self.threshold_audit_relative_step <= 0.5:
            raise ArchetypeConfigError("threshold_audit_relative_step must be within (0, 0.5]")
        if self.threshold_audit_quantile_bins < 2:
            raise ArchetypeConfigError("threshold_audit_quantile_bins must be at least two")
        if self.threshold_audit_bootstrap_iterations < 200:
            raise ArchetypeConfigError("threshold_audit_bootstrap_iterations must be at least 200")
        if len(self.shuffled_seeds) < math.ceil(1.0 / self.control_empirical_alpha) - 1:
            raise ArchetypeConfigError(
                "shuffled_seeds are too few for the registered empirical control alpha"
            )
        if self.iterations <= 0 or self.depth <= 0 or self.depth > 8:
            raise ArchetypeConfigError("iterations must be positive and depth must be within [1, 8]")
        if self.learning_rate <= 0.0 or self.l2_leaf_reg < 0.0:
            raise ArchetypeConfigError("learning_rate must be positive and l2_leaf_reg non-negative")
        if self.catboost_thread_count <= 0:
            raise ArchetypeConfigError("catboost_thread_count must be positive; use an explicit bounded value")
        if len(set(self.generators)) != len(self.generators):
            raise ArchetypeConfigError("generators must be unique")
        if not self.rolling_origin_fractions:
            raise ArchetypeConfigError("at least one rolling_origin_fraction is required")
        if tuple(sorted(set(self.rolling_origin_fractions))) != self.rolling_origin_fractions:
            raise ArchetypeConfigError("rolling_origin_fractions must be unique and increasing")
        if any(not 0.0 < value <= 1.0 for value in self.rolling_origin_fractions):
            raise ArchetypeConfigError("rolling_origin_fractions must be within (0, 1]")
        if self.rolling_origin_fractions[-1] != 1.0:
            raise ArchetypeConfigError("rolling_origin_fractions must end at 1.0")
        for name in ("min_origin_support_fraction", "min_generator_support_fraction"):
            if not 0.0 < getattr(self, name) <= 1.0:
                raise ArchetypeConfigError(f"{name} must be within (0, 1]")
        if not 0.0 < self.consensus_membership_jaccard <= 1.0:
            raise ArchetypeConfigError("consensus_membership_jaccard must be within (0, 1]")
        if self.min_discovery_events <= 0 or self.min_verification_events <= 0:
            raise ArchetypeConfigError("minimum event counts must be positive")
        for name in ("min_discovery_fade_rate", "min_verification_fade_rate", "fdr_alpha"):
            value = getattr(self, name)
            if not 0.0 < value < 1.0:
                raise ArchetypeConfigError(f"{name} must be within (0, 1)")
        if self.min_discovery_lift <= 1.0 or self.min_verification_lift <= 1.0:
            raise ArchetypeConfigError("minimum lifts must be greater than one")
        if not 0.0 <= self.max_membership_jaccard < 1.0:
            raise ArchetypeConfigError("max_membership_jaccard must be within [0, 1)")
        if not 0.0 < self.min_unique_event_fraction <= 1.0:
            raise ArchetypeConfigError("min_unique_event_fraction must be within (0, 1]")
        if self.max_categories is not None and self.max_categories <= 0:
            raise ArchetypeConfigError("max_categories must be positive or null")
        if self.min_events_per_stability_period <= 0:
            raise ArchetypeConfigError("min_events_per_stability_period must be positive")
        if not 0.0 <= self.min_positive_stability_fraction <= 1.0:
            raise ArchetypeConfigError("min_positive_stability_fraction must be within [0, 1]")
        if self.matched_control_quantiles < 2:
            raise ArchetypeConfigError("matched_control_quantiles must be at least two")
        if self.min_matched_control_rows <= 0 or self.block_bootstrap_iterations < 200:
            raise ArchetypeConfigError(
                "min_matched_control_rows must be positive and block_bootstrap_iterations at least 200"
            )
        if self.min_inference_blocks < 4:
            raise ArchetypeConfigError("min_inference_blocks must be at least four")
        if not 0.0 < self.min_matched_signal_fraction <= 1.0:
            raise ArchetypeConfigError("min_matched_signal_fraction must be within (0, 1]")
        if self.evidence_mode not in {"development", "pristine_holdout"}:
            raise ArchetypeConfigError("evidence_mode must be development or pristine_holdout")
        if self.evidence_mode == "pristine_holdout" and not self.protocol_freeze_id.strip():
            raise ArchetypeConfigError(
                "pristine_holdout evidence requires a protocol_freeze_id created before holdout access"
            )
        feature_columns = set(self.features.numeric) | set(self.features.decision_timing_numeric) | set(
            self.features.categorical
        )
        missing_match = (
            set(self.matched_control_columns) | set(self.matched_control_quantile_columns)
        ) - feature_columns
        if missing_match:
            raise ArchetypeConfigError(
                f"matched-control columns must be explicit causal features: {sorted(missing_match)}"
            )

    @property
    def discovery_start_ms(self) -> int:
        return parse_utc_timestamp_ms(self.discovery_start_utc, field_name="discovery_start_utc")

    @property
    def discovery_end_ms(self) -> int:
        return parse_utc_timestamp_ms(self.discovery_end_utc, field_name="discovery_end_utc")

    @property
    def verification_start_ms(self) -> int:
        return parse_utc_timestamp_ms(self.verification_start_utc, field_name="verification_start_utc")

    @property
    def verification_end_ms(self) -> int:
        return parse_utc_timestamp_ms(self.verification_end_utc, field_name="verification_end_utc")

    @property
    def effective_generators(self) -> tuple[ArchetypeGeneratorSpec, ...]:
        return self.generators or (
            ArchetypeGeneratorSpec(random_seed=self.random_seed, depth=self.depth),
        )


def _tuple_strings(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ArchetypeConfigError(f"{field_name} must be a JSON array of non-empty strings")
    return tuple(value)


def load_archetype_discovery_config(path: Path) -> ArchetypeDiscoveryConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ArchetypeConfigError("archetype config root must be a JSON object")
    input_raw = dict(raw.pop("input"))
    feature_raw = dict(raw.pop("features"))
    population_raw = dict(raw.pop("population", {}))
    feature_spec = ArchetypeFeatureSpec(
        numeric=_tuple_strings(feature_raw.pop("numeric"), field_name="features.numeric"),
        decision_timing_numeric=_tuple_strings(
            feature_raw.pop("decision_timing_numeric", []),
            field_name="features.decision_timing_numeric",
        ),
        categorical=_tuple_strings(feature_raw.pop("categorical", []), field_name="features.categorical"),
        derived_time=_tuple_strings(feature_raw.pop("derived_time", []), field_name="features.derived_time"),
        include_decision_timing=bool(feature_raw.pop("include_decision_timing", False)),
    )
    if feature_raw:
        raise ArchetypeConfigError(f"unknown feature config keys: {sorted(feature_raw)}")
    shuffled_seeds = raw.pop(
        "shuffled_seeds",
        [
            11,
            29,
            47,
            61,
            79,
            97,
            113,
            131,
            149,
            167,
            181,
            199,
            223,
            241,
            263,
            281,
            307,
            331,
            353,
        ],
    )
    if not isinstance(shuffled_seeds, list) or any(not isinstance(seed, int) for seed in shuffled_seeds):
        raise ArchetypeConfigError("shuffled_seeds must be a JSON array of integers")
    matched_control_columns = _tuple_strings(
        raw.pop("matched_control_columns", []), field_name="matched_control_columns"
    )
    matched_control_quantile_columns = _tuple_strings(
        raw.pop("matched_control_quantile_columns", []),
        field_name="matched_control_quantile_columns",
    )
    generator_raw = raw.pop("generators", [])
    if not isinstance(generator_raw, list) or any(not isinstance(item, dict) for item in generator_raw):
        raise ArchetypeConfigError("generators must be a JSON array of objects")
    generators = tuple(ArchetypeGeneratorSpec(**item) for item in generator_raw)
    rolling_origin_raw = raw.pop("rolling_origin_fractions", [0.60, 0.80, 1.0])
    if not isinstance(rolling_origin_raw, list) or any(
        not isinstance(item, (int, float)) for item in rolling_origin_raw
    ):
        raise ArchetypeConfigError("rolling_origin_fractions must be a JSON array of numbers")
    required_values_raw = population_raw.pop("required_values", [])
    if not isinstance(required_values_raw, list) or any(
        not isinstance(item, dict) for item in required_values_raw
    ):
        raise ArchetypeConfigError("population.required_values must be a JSON array of objects")
    required_values = tuple(ArchetypeRequiredValue(**item) for item in required_values_raw)
    required_finite_columns = _tuple_strings(
        population_raw.pop("required_finite_columns", []),
        field_name="population.required_finite_columns",
    )
    return ArchetypeDiscoveryConfig(
        input=ArchetypeInputContract(**input_raw),
        features=feature_spec,
        population=ArchetypePopulationSpec(
            required_values=required_values,
            required_finite_columns=required_finite_columns,
            **population_raw,
        ),
        shuffled_seeds=tuple(shuffled_seeds),
        generators=generators,
        rolling_origin_fractions=tuple(float(item) for item in rolling_origin_raw),
        matched_control_columns=matched_control_columns,
        matched_control_quantile_columns=matched_control_quantile_columns,
        **raw,
    )


def config_to_json_dict(config: ArchetypeDiscoveryConfig) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(config)
