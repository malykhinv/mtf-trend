from __future__ import annotations

import json
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
    feature_cutoff_time_column: str | None = None
    future_start_time_column: str | None = None
    attest_feature_cutoff_equals_snapshot: bool = False
    future_start_offset_ms: int | None = None

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


@dataclass(frozen=True, slots=True)
class ArchetypeFeatureSpec:
    numeric: tuple[str, ...]
    categorical: tuple[str, ...] = ()
    derived_time: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        all_columns = (*self.numeric, *self.categorical)
        if not all_columns:
            raise ArchetypeConfigError("at least one explicit causal feature is required")
        if len(set(all_columns)) != len(all_columns):
            raise ArchetypeConfigError("numeric and categorical feature columns must be unique")
        unknown = set(self.derived_time) - SUPPORTED_TIME_FEATURES
        if unknown:
            raise ArchetypeConfigError(f"unknown derived_time features: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class ArchetypePopulationSpec:
    minimum_value_column: str | None = None
    minimum_value: float | None = None

    def __post_init__(self) -> None:
        if (self.minimum_value_column is None) != (self.minimum_value is None):
            raise ArchetypeConfigError(
                "minimum_value_column and minimum_value must either both be set or both be omitted"
            )


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
    min_discovery_events: int = 80
    min_verification_events: int = 50
    min_discovery_fade_rate: float = 0.58
    min_verification_fade_rate: float = 0.58
    min_discovery_lift: float = 1.15
    min_verification_lift: float = 1.15
    max_membership_jaccard: float = 0.80
    max_categories: int = 30
    fdr_alpha: float = 0.05
    stability_frequency: str = "M"
    min_events_per_stability_period: int = 15
    min_positive_stability_fraction: float = 0.60
    shuffled_seeds: tuple[int, ...] = (11, 29, 47)

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
        if self.iterations <= 0 or self.depth <= 0 or self.depth > 8:
            raise ArchetypeConfigError("iterations must be positive and depth must be within [1, 8]")
        if self.learning_rate <= 0.0 or self.l2_leaf_reg < 0.0:
            raise ArchetypeConfigError("learning_rate must be positive and l2_leaf_reg non-negative")
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
        if self.max_categories <= 0:
            raise ArchetypeConfigError("max_categories must be positive")
        if self.min_events_per_stability_period <= 0:
            raise ArchetypeConfigError("min_events_per_stability_period must be positive")
        if not 0.0 <= self.min_positive_stability_fraction <= 1.0:
            raise ArchetypeConfigError("min_positive_stability_fraction must be within [0, 1]")

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
        categorical=_tuple_strings(feature_raw.pop("categorical", []), field_name="features.categorical"),
        derived_time=_tuple_strings(feature_raw.pop("derived_time", []), field_name="features.derived_time"),
    )
    if feature_raw:
        raise ArchetypeConfigError(f"unknown feature config keys: {sorted(feature_raw)}")
    shuffled_seeds = raw.pop("shuffled_seeds", [11, 29, 47])
    if not isinstance(shuffled_seeds, list) or any(not isinstance(seed, int) for seed in shuffled_seeds):
        raise ArchetypeConfigError("shuffled_seeds must be a JSON array of integers")
    return ArchetypeDiscoveryConfig(
        input=ArchetypeInputContract(**input_raw),
        features=feature_spec,
        population=ArchetypePopulationSpec(**population_raw),
        shuffled_seeds=tuple(shuffled_seeds),
        **raw,
    )


def config_to_json_dict(config: ArchetypeDiscoveryConfig) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(config)
