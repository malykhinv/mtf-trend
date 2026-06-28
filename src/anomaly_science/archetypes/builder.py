from __future__ import annotations

import json
import math
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score

from anomaly_science.archetypes.config import (
    ArchetypeDiscoveryConfig,
    ArchetypeGeneratorSpec,
)
from anomaly_science.contracts.archetypes import (
    ArchetypeCategoryRow,
    ArchetypeCandidateFunnelRow,
    ArchetypeControlRow,
    ArchetypeCoverageRow,
)
from anomaly_science.contracts.time import TemporalContractError


ARCHETYPE_TEMPORAL_CONTRACT = (
    "explicit_features<=snapshot_time;future_start_time>snapshot_time;"
    "discovery_labels_resolve_before_discovery_end;frozen_rules_verified_later"
)
FIRST_SIGNAL_POLICY = "first_matching_snapshot_per_group"


class ArchetypeDiscoveryError(ValueError):
    """Raised when an input dataset cannot support honest archetype discovery."""


@dataclass(slots=True)
class PreparedArchetypeData:
    discovery: pd.DataFrame
    verification: pd.DataFrame
    x_discovery: pd.DataFrame
    x_verification: pd.DataFrame
    model_feature_names: tuple[str, ...]
    dropped_constant_features: tuple[str, ...]
    unknown_verification_categories: tuple[str, ...]


@dataclass(slots=True)
class RuleCandidate:
    tree_index: int
    leaf_index: int
    rule: tuple[dict[str, Any], ...]
    rule_text: str
    feature_names: tuple[str, ...]
    discovery_indices: np.ndarray
    discovery_groups: frozenset[str]
    discovery_count: int
    discovery_fades: int
    discovery_rate: float
    discovery_lift: float
    discovery_lower: float
    discovery_matched_rate: float
    discovery_edge_lower: float
    discovery_matched_count: int
    discovery_unique_fraction: float = 0.0
    discovery_inference_blocks: int = 0
    verification_indices: np.ndarray | None = None
    verification_count: int = 0
    verification_fades: int = 0
    verification_rate: float = 0.0
    verification_lift: float = 0.0
    verification_lower: float = 0.0
    verification_matched_rate: float = 0.0
    verification_edge_lower: float = 0.0
    verification_matched_count: int = 0
    verification_inference_blocks: int = 0
    verification_p: float = 1.0
    verification_q: float = 1.0
    stability_periods: int = 0
    positive_period_fraction: float = 0.0
    generator_seed: int = 0
    generator_depth: int = 0
    origin_fraction: float = 1.0
    origin_id: str = "origin_1.000"
    reference_indices: np.ndarray | None = None
    reference_groups: frozenset[str] = frozenset()
    origin_support_count: int = 0
    origin_support_fraction: float = 0.0
    generator_support_count: int = 0
    generator_support_fraction: float = 0.0


@dataclass(slots=True)
class ArchetypeBuildResult:
    category_rows: list[ArchetypeCategoryRow]
    control_rows: list[ArchetypeControlRow]
    coverage_rows: list[ArchetypeCoverageRow]
    assignments: pd.DataFrame
    model: CatBoostClassifier
    model_json: dict[str, Any]
    prepared: PreparedArchetypeData
    discovery_candidate_count: int
    distinct_candidate_count: int
    verification_auc: float
    generator_fit_count: int
    search_truncated: bool
    controls_passed: bool
    auc_control_empirical_p: float
    category_count_control_empirical_p: float
    candidate_funnel_rows: list[ArchetypeCandidateFunnelRow]


def _required_input_columns(config: ArchetypeDiscoveryConfig) -> set[str]:
    contract = config.input
    required = {
        contract.group_column,
        contract.symbol_column,
        contract.label_column,
        contract.snapshot_time_column,
        contract.resolution_time_column,
        *config.features.numeric,
        *config.features.decision_timing_numeric,
        *config.features.categorical,
    }
    if contract.feature_cutoff_time_column:
        required.add(contract.feature_cutoff_time_column)
    if contract.future_start_time_column:
        required.add(contract.future_start_time_column)
    if contract.label_available_column:
        required.add(contract.label_available_column)
    if contract.label_schema_column:
        required.add(contract.label_schema_column)
    if contract.row_filter_column:
        required.add(contract.row_filter_column)
    if config.population.minimum_value_column:
        required.add(config.population.minimum_value_column)
    return required


def _validate_feature_boundary(config: ArchetypeDiscoveryConfig) -> None:
    contract = config.input
    forbidden = {
        contract.group_column,
        contract.symbol_column,
        contract.label_column,
        contract.snapshot_time_column,
        contract.resolution_time_column,
    }
    if contract.feature_cutoff_time_column:
        forbidden.add(contract.feature_cutoff_time_column)
    if contract.future_start_time_column:
        forbidden.add(contract.future_start_time_column)
    if contract.row_filter_column:
        forbidden.add(contract.row_filter_column)
    selected = (
        set(config.features.numeric)
        | set(config.features.decision_timing_numeric)
        | set(config.features.categorical)
    )
    overlap = selected & forbidden
    if overlap:
        raise ArchetypeDiscoveryError(
            f"identity, target, and future columns cannot be model features: {sorted(overlap)}"
        )


def _coerce_int_time(frame: pd.DataFrame, column: str) -> np.ndarray:
    values = pd.to_numeric(frame[column], errors="raise").to_numpy()
    if not np.all(np.isfinite(values)):
        raise TemporalContractError(f"{column} contains missing or non-finite timestamps")
    if not np.all(values == values.astype(np.int64)):
        raise TemporalContractError(f"{column} must contain integer Unix milliseconds")
    values = values.astype(np.int64)
    if np.any(values < 0):
        raise TemporalContractError(f"{column} contains negative timestamps")
    return values


def _time_arrays(frame: pd.DataFrame, config: ArchetypeDiscoveryConfig) -> tuple[np.ndarray, ...]:
    contract = config.input
    snapshot = _coerce_int_time(frame, contract.snapshot_time_column)
    resolution_values = pd.to_numeric(frame[contract.resolution_time_column], errors="coerce")
    resolution = resolution_values.to_numpy(dtype=float)
    if contract.feature_cutoff_time_column:
        cutoff = _coerce_int_time(frame, contract.feature_cutoff_time_column)
    else:
        cutoff = snapshot.copy()
    if contract.future_start_time_column:
        future_start = _coerce_int_time(frame, contract.future_start_time_column)
    else:
        assert contract.future_start_offset_ms is not None
        future_start = snapshot + contract.future_start_offset_ms
    if np.any(cutoff > snapshot):
        raise TemporalContractError("feature_cutoff_time must be <= snapshot_time for every row")
    if np.any(future_start <= snapshot):
        raise TemporalContractError("future_start_time must be > snapshot_time for every row")
    available = (
        frame[contract.label_available_column].astype(bool).to_numpy()
        if contract.label_available_column
        else np.ones(len(frame), dtype=bool)
    )
    if np.any(available & ~np.isfinite(resolution)):
        raise TemporalContractError("available labels require a finite resolution_time")
    if np.any(available & (resolution < future_start)):
        raise TemporalContractError("resolution_time must be >= future_start_time for every labeled row")
    return snapshot, cutoff, future_start, resolution


def _derive_time_features(snapshot: pd.Series, names: tuple[str, ...]) -> pd.DataFrame:
    time = pd.to_datetime(snapshot, unit="ms", utc=True)
    minute = time.dt.minute.astype(np.float32)
    result: dict[str, pd.Series] = {}
    for name in names:
        if name == "hour_utc":
            result[name] = time.dt.hour.astype(np.float32)
        elif name == "minute_of_hour":
            result[name] = minute
        elif name == "minutes_from_round_hour":
            result[name] = np.minimum(minute, 60.0 - minute).astype(np.float32)
        elif name == "day_of_week_utc":
            result[name] = time.dt.dayofweek.astype(np.float32)
        else:  # validated by config, kept defensive for direct internal callers
            raise ArchetypeDiscoveryError(f"unsupported derived time feature: {name}")
    return pd.DataFrame(result, index=snapshot.index)


def _make_model_matrices(
    discovery: pd.DataFrame,
    verification: pd.DataFrame,
    config: ArchetypeDiscoveryConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, tuple[str, ...], tuple[str, ...]]:
    disc_parts: list[pd.DataFrame] = []
    ver_parts: list[pd.DataFrame] = []
    unknown_categories: list[str] = []
    numeric_columns = list(config.features.numeric)
    if config.features.include_decision_timing:
        numeric_columns.extend(config.features.decision_timing_numeric)
    for column in numeric_columns:
        disc_values = pd.to_numeric(discovery[column], errors="raise").astype(np.float32)
        ver_values = pd.to_numeric(verification[column], errors="raise").astype(np.float32)
        if np.isinf(disc_values.to_numpy()).any() or np.isinf(ver_values.to_numpy()).any():
            raise ArchetypeDiscoveryError(f"numeric feature {column!r} contains infinity")
        if disc_values.isna().any() or ver_values.isna().any():
            raise ArchetypeDiscoveryError(
                f"numeric feature {column!r} contains missing values; explicit causal imputation "
                "must occur before rule discovery so exported inequalities remain reproducible"
            )
        disc_parts.append(disc_values.to_frame(column))
        ver_parts.append(ver_values.to_frame(column))
    for column in config.features.categorical:
        disc_raw = discovery[column].astype("string").fillna("__MISSING__")
        ver_raw = verification[column].astype("string").fillna("__MISSING__")
        categories = sorted(str(value) for value in disc_raw.unique())
        unknown = sorted(set(str(value) for value in ver_raw.unique()) - set(categories))
        unknown_categories.extend(f"{column}={value}" for value in unknown)
        for category in categories:
            feature_name = f"{column}=={category}"
            disc_parts.append((disc_raw == category).astype(np.float32).to_frame(feature_name))
            ver_parts.append((ver_raw == category).astype(np.float32).to_frame(feature_name))
    if config.features.derived_time:
        disc_parts.append(
            _derive_time_features(discovery[config.input.snapshot_time_column], config.features.derived_time)
        )
        ver_parts.append(
            _derive_time_features(verification[config.input.snapshot_time_column], config.features.derived_time)
        )
    x_discovery = pd.concat(disc_parts, axis=1)
    x_verification = pd.concat(ver_parts, axis=1)
    dropped: list[str] = []
    keep: list[str] = []
    for column in x_discovery.columns:
        values = x_discovery[column]
        if values.notna().sum() == 0:
            raise ArchetypeDiscoveryError(f"feature {column!r} is entirely missing in discovery")
        if values.nunique(dropna=False) <= 1:
            dropped.append(column)
        else:
            keep.append(column)
    if not keep:
        raise ArchetypeDiscoveryError("all model features are constant in discovery")
    return x_discovery[keep], x_verification[keep], tuple(dropped), tuple(unknown_categories)


def prepare_archetype_data(frame: pd.DataFrame, config: ArchetypeDiscoveryConfig) -> PreparedArchetypeData:
    _validate_feature_boundary(config)
    missing = sorted(_required_input_columns(config) - set(frame.columns))
    if missing:
        raise ArchetypeDiscoveryError(f"input dataset is missing required columns: {missing}")
    if frame.empty:
        raise ArchetypeDiscoveryError("input dataset is empty")
    work = frame.copy()
    contract = config.input
    if contract.label_schema_column:
        observed_schemas = set(work[contract.label_schema_column].dropna().astype(str).unique())
        if observed_schemas != {contract.required_label_schema_value}:
            raise ArchetypeDiscoveryError(
                f"label schema mismatch: expected {contract.required_label_schema_value!r}, "
                f"observed {sorted(observed_schemas)}"
            )
    if work[contract.group_column].isna().any() or work[contract.symbol_column].isna().any():
        raise ArchetypeDiscoveryError("group and symbol columns cannot contain missing values")
    if contract.row_filter_column is not None:
        work = work[
            work[contract.row_filter_column] == contract.required_row_filter_value
        ].copy()
        if work.empty:
            raise ArchetypeDiscoveryError("registered row filter removed every row")
    work["__snapshot_ms"] = _coerce_int_time(work, contract.snapshot_time_column)
    if config.population.minimum_value_column is not None:
        population_values = pd.to_numeric(
            work[config.population.minimum_value_column], errors="raise"
        )
        work = work[population_values >= float(config.population.minimum_value)].copy()
    if work.empty:
        raise ArchetypeDiscoveryError("population filter removed every row")

    group_bounds = work.groupby(contract.group_column, sort=False)["__snapshot_ms"].agg(["min", "max"])
    if contract.label_available_column:
        work = work[work[contract.label_available_column].astype(bool)].copy()
    else:
        resolution_values = pd.to_numeric(
            work[contract.resolution_time_column], errors="coerce"
        )
        work = work[np.isfinite(resolution_values)].copy()
    if work.empty:
        raise ArchetypeDiscoveryError("no registered rows have an available label")
    snapshot, cutoff, future_start, resolution = _time_arrays(work, config)
    work["__snapshot_ms"] = snapshot
    work["__feature_cutoff_ms"] = cutoff
    work["__future_start_ms"] = future_start
    work["__resolution_ms"] = resolution
    labels = pd.to_numeric(work[contract.label_column], errors="raise")
    if not set(labels.unique()).issubset({0, 1}):
        raise ArchetypeDiscoveryError("available label rows must contain binary 0/1 targets")
    work[contract.label_column] = labels.astype(np.int8)
    discovery_groups = group_bounds.index[
        (group_bounds["min"] >= config.discovery_start_ms)
        & (group_bounds["max"] < config.discovery_end_ms)
    ]
    verification_groups = group_bounds.index[
        (group_bounds["min"] >= config.verification_start_ms)
        & (group_bounds["max"] < config.verification_end_ms)
    ]
    discovery = work[
        work[contract.group_column].isin(discovery_groups)
        & (work["__resolution_ms"] < config.discovery_end_ms)
    ].copy()
    verification = work[
        work[contract.group_column].isin(verification_groups)
        & (work["__resolution_ms"] < config.verification_end_ms)
    ].copy()
    if discovery.empty or verification.empty:
        raise ArchetypeDiscoveryError(
            "both discovery and verification must contain resolution-purged labeled rows"
        )
    overlap = set(discovery[contract.group_column]) & set(verification[contract.group_column])
    if overlap:
        raise ArchetypeDiscoveryError("anomaly groups cannot cross discovery and verification")
    sort_columns = ["__snapshot_ms", contract.group_column]
    discovery = discovery.sort_values(sort_columns, kind="mergesort").reset_index(drop=True)
    verification = verification.sort_values(sort_columns, kind="mergesort").reset_index(drop=True)
    x_discovery, x_verification, dropped, unknown = _make_model_matrices(
        discovery, verification, config
    )
    return PreparedArchetypeData(
        discovery=discovery,
        verification=verification,
        x_discovery=x_discovery.reset_index(drop=True),
        x_verification=x_verification.reset_index(drop=True),
        model_feature_names=tuple(x_discovery.columns),
        dropped_constant_features=dropped,
        unknown_verification_categories=unknown,
    )


def _group_inverse_weights(frame: pd.DataFrame, group_column: str) -> np.ndarray:
    sizes = frame.groupby(group_column)[group_column].transform("size").to_numpy(dtype=float)
    return 1.0 / sizes


def fit_rule_generator(
    prepared: PreparedArchetypeData,
    config: ArchetypeDiscoveryConfig,
    *,
    labels: np.ndarray | None = None,
    random_seed: int | None = None,
    depth: int | None = None,
) -> CatBoostClassifier:
    target = (
        prepared.discovery[config.input.label_column].to_numpy(dtype=np.int8)
        if labels is None
        else np.asarray(labels, dtype=np.int8)
    )
    if len(target) != len(prepared.discovery) or len(np.unique(target)) != 2:
        raise ArchetypeDiscoveryError("CatBoost discovery target must contain both binary classes")
    model = CatBoostClassifier(
        iterations=config.iterations,
        depth=config.depth if depth is None else depth,
        learning_rate=config.learning_rate,
        l2_leaf_reg=config.l2_leaf_reg,
        loss_function="Logloss",
        eval_metric="AUC",
        random_seed=config.random_seed if random_seed is None else random_seed,
        verbose=False,
        allow_writing_files=False,
        thread_count=-1,
    )
    model.fit(
        prepared.x_discovery,
        target,
        sample_weight=_group_inverse_weights(prepared.discovery, config.input.group_column),
    )
    return model


def export_model_json(model: CatBoostClassifier) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        path = Path(handle.name)
    try:
        model.save_model(str(path), format="json")
        return json.loads(path.read_text(encoding="utf-8"))
    finally:
        path.unlink(missing_ok=True)


def _subset_prepared(
    prepared: PreparedArchetypeData, positions: np.ndarray
) -> PreparedArchetypeData:
    return PreparedArchetypeData(
        discovery=prepared.discovery.iloc[positions].reset_index(drop=True),
        verification=prepared.verification,
        x_discovery=prepared.x_discovery.iloc[positions].reset_index(drop=True),
        x_verification=prepared.x_verification,
        model_feature_names=prepared.model_feature_names,
        dropped_constant_features=prepared.dropped_constant_features,
        unknown_verification_categories=prepared.unknown_verification_categories,
    )


def _origin_positions(
    prepared: PreparedArchetypeData,
    config: ArchetypeDiscoveryConfig,
    fraction: float,
) -> np.ndarray:
    frame = prepared.discovery
    group_column = config.input.group_column
    if fraction == 1.0:
        return np.arange(len(frame), dtype=np.int64)
    bounds = frame.groupby(group_column, sort=False).agg(
        first_snapshot=("__snapshot_ms", "min"),
        last_snapshot=("__snapshot_ms", "max"),
        last_resolution=("__resolution_ms", "max"),
    ).sort_values(["first_snapshot"], kind="mergesort")
    requested = max(1, int(math.floor(len(bounds) * fraction)))
    if requested >= len(bounds):
        return np.arange(len(frame), dtype=np.int64)
    cutoff = int(bounds.iloc[requested]["first_snapshot"])
    eligible_groups = bounds.index[
        (bounds["last_snapshot"] < cutoff) & (bounds["last_resolution"] < cutoff)
    ]
    mask = frame[group_column].isin(eligible_groups).to_numpy()
    positions = np.flatnonzero(mask)
    if not len(positions):
        raise ArchetypeDiscoveryError(
            f"rolling origin {fraction:.3f} has no resolution-purged groups"
        )
    return positions


def _first_matching_rule_rows(
    frame: pd.DataFrame,
    matrix: pd.DataFrame,
    rule: tuple[dict[str, Any], ...],
    *,
    group_column: str,
) -> np.ndarray:
    mask = np.ones(len(matrix), dtype=bool)
    for condition in rule:
        values = matrix[str(condition["feature"])].to_numpy(dtype=float)
        threshold = float(condition["value"])
        operator = str(condition["operator"])
        if operator == ">":
            mask &= values > threshold
        elif operator == "<=":
            mask &= values <= threshold
        else:
            raise ArchetypeDiscoveryError(f"unsupported rule operator: {operator}")
    matching = np.flatnonzero(mask)
    if not len(matching):
        return np.array([], dtype=np.int64)
    matched_frame = frame.iloc[matching]
    first = ~matched_frame[group_column].astype(str).duplicated(keep="first")
    return matching[first.to_numpy()]


def _membership_jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = len(left | right)
    return len(left & right) / union if union else 1.0


def _canonical_rule(
    tree: dict[str, Any], leaf_index: int, model_json: dict[str, Any]
) -> tuple[tuple[dict[str, Any], ...], str, tuple[str, ...]]:
    float_names = {
        int(item["feature_index"]): str(item["feature_id"])
        for item in model_json["features_info"]["float_features"]
    }
    bounds: dict[str, dict[str, float]] = {}
    for depth_index, split in enumerate(tree.get("splits") or []):
        if split.get("split_type") != "FloatFeature":
            raise ArchetypeDiscoveryError(
                "rule extraction only supports explicit numeric/one-hot float features"
            )
        feature_name = float_names[int(split["float_feature_index"])]
        border = float(split["border"])
        feature_bounds = bounds.setdefault(feature_name, {})
        if leaf_index & (1 << depth_index):
            feature_bounds["lower_exclusive"] = max(
                border, feature_bounds.get("lower_exclusive", -math.inf)
            )
        else:
            feature_bounds["upper_inclusive"] = min(
                border, feature_bounds.get("upper_inclusive", math.inf)
            )
    conditions: list[dict[str, Any]] = []
    text: list[str] = []
    for feature_name in sorted(bounds):
        feature_bounds = bounds[feature_name]
        if "lower_exclusive" in feature_bounds:
            value = feature_bounds["lower_exclusive"]
            conditions.append({"feature": feature_name, "operator": ">", "value": value})
            text.append(f"{feature_name} > {value:.8g}")
        if "upper_inclusive" in feature_bounds:
            value = feature_bounds["upper_inclusive"]
            conditions.append({"feature": feature_name, "operator": "<=", "value": value})
            text.append(f"{feature_name} <= {value:.8g}")
    return tuple(conditions), " AND ".join(text), tuple(sorted(bounds))


def _matched_probabilities(
    frame: pd.DataFrame,
    config: ArchetypeDiscoveryConfig,
    *,
    labels: np.ndarray | None = None,
) -> np.ndarray:
    target = (
        frame[config.input.label_column].to_numpy(dtype=np.int8)
        if labels is None
        else np.asarray(labels, dtype=np.int8)
    )
    strata = pd.DataFrame(index=frame.index)
    strata["calendar_month"] = pd.to_datetime(
        frame["__snapshot_ms"], unit="ms", utc=True
    ).dt.strftime("%Y-%m")
    for column in config.matched_control_columns:
        strata[f"exact:{column}"] = frame[column].astype("string").fillna("__MISSING__")
    for column in config.matched_control_quantile_columns:
        values = pd.to_numeric(frame[column], errors="raise")
        ranked = values.rank(method="first")
        strata[f"quantile:{column}"] = pd.qcut(
            ranked,
            q=config.matched_control_quantiles,
            labels=False,
            duplicates="drop",
        ).astype("Int64")
    stratum_key = pd.util.hash_pandas_object(strata, index=False).to_numpy(dtype=np.uint64)
    groups = frame[config.input.group_column].astype(str).to_numpy()
    pair = pd.DataFrame({"group": groups, "stratum": stratum_key})
    pair_counts = pair.groupby(["group", "stratum"], sort=False)["group"].transform("size")
    weights = 1.0 / pair_counts.to_numpy(dtype=float)
    summary = pd.DataFrame(
        {
            "stratum": stratum_key,
            "weighted_target": target * weights,
            "weight": weights,
            "group": groups,
        }
    ).groupby("stratum", sort=False).agg(
        weighted_target=("weighted_target", "sum"),
        weight=("weight", "sum"),
        group_count=("group", "nunique"),
    )
    rates = summary["weighted_target"] / summary["weight"]
    valid_rates = rates.where(summary["group_count"] >= config.min_matched_control_rows)
    return pd.Series(stratum_key).map(valid_rates).to_numpy(dtype=float)


def _cluster_bootstrap(
    *,
    frame: pd.DataFrame,
    indices: np.ndarray,
    labels: np.ndarray,
    matched_probability: np.ndarray,
    iterations: int,
    seed: int,
) -> tuple[float, float, float, int]:
    if len(indices) == 0:
        return 0.0, 0.0, 1.0, 0
    selected_labels = labels[indices].astype(float)
    matched = matched_probability[indices]
    if np.any(~np.isfinite(matched)):
        return 0.0, -1.0, 1.0, 0
    block_time = pd.to_datetime(frame.loc[indices, "__snapshot_ms"], unit="ms", utc=True)
    iso = block_time.dt.isocalendar()
    block = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    block_positions = [
        np.flatnonzero(block.to_numpy() == value) for value in pd.unique(block)
    ]
    rng = np.random.default_rng(seed)
    rate_samples = np.empty(iterations, dtype=float)
    edge_samples = np.empty(iterations, dtype=float)
    residual = selected_labels - matched
    block_residual_sums = np.asarray([residual[position].sum() for position in block_positions])
    observed_edge = float(residual.mean())
    null_edges = np.empty(iterations, dtype=float)
    for iteration in range(iterations):
        sampled_blocks = rng.integers(0, len(block_positions), size=len(block_positions))
        sampled = np.concatenate([block_positions[index] for index in sampled_blocks])
        rate_samples[iteration] = float(selected_labels[sampled].mean())
        edge_samples[iteration] = float((selected_labels[sampled] - matched[sampled]).mean())
        signs = rng.choice(np.asarray([-1.0, 1.0]), size=len(block_positions))
        null_edges[iteration] = float(np.sum(signs * block_residual_sums) / len(residual))
    lower_rate = float(np.quantile(rate_samples, 0.025))
    lower_edge = float(np.quantile(edge_samples, 0.025))
    p_value = float((1 + np.count_nonzero(null_edges >= observed_edge)) / (iterations + 1))
    return lower_rate, lower_edge, p_value, len(block_positions)


def _first_rows_per_group(frame: pd.DataFrame, group_column: str) -> np.ndarray:
    return frame.drop_duplicates(group_column, keep="first").index.to_numpy(dtype=np.int64)


def _blind_rate(frame: pd.DataFrame, config: ArchetypeDiscoveryConfig) -> tuple[int, float]:
    first = _first_rows_per_group(frame, config.input.group_column)
    labels = frame.loc[first, config.input.label_column].to_numpy(dtype=np.int8)
    return len(labels), float(labels.mean())


def _first_leaf_rows(
    frame: pd.DataFrame,
    leaf_values: np.ndarray,
    *,
    group_column: str,
    leaf_count: int,
) -> dict[int, np.ndarray]:
    groups, _ = pd.factorize(frame[group_column], sort=False)
    composite = groups.astype(np.int64) * int(leaf_count) + leaf_values.astype(np.int64)
    _, first_positions = np.unique(composite, return_index=True)
    first_positions = np.sort(first_positions)
    result: dict[int, np.ndarray] = {}
    first_leaf = leaf_values[first_positions]
    for leaf in np.unique(first_leaf):
        result[int(leaf)] = first_positions[first_leaf == leaf]
    return result


def discover_candidates(
    *,
    model: CatBoostClassifier,
    model_json: dict[str, Any],
    prepared: PreparedArchetypeData,
    config: ArchetypeDiscoveryConfig,
    discovery_labels: np.ndarray | None = None,
    matched_probability: np.ndarray | None = None,
    funnel: Counter[str] | None = None,
) -> list[RuleCandidate]:
    frame = prepared.discovery
    labels = (
        frame[config.input.label_column].to_numpy(dtype=np.int8)
        if discovery_labels is None
        else np.asarray(discovery_labels, dtype=np.int8)
    )
    if matched_probability is None:
        matched_probability = _matched_probabilities(frame, config, labels=labels)
    leaf_matrix = np.asarray(model.calc_leaf_indexes(prepared.x_discovery))
    trees = model_json["oblivious_trees"]
    candidates: list[RuleCandidate] = []
    for tree_index, tree in enumerate(trees):
        leaf_count = len(tree["leaf_values"])
        first_by_leaf = _first_leaf_rows(
            frame,
            leaf_matrix[:, tree_index],
            group_column=config.input.group_column,
            leaf_count=leaf_count,
        )
        for leaf_index, indices in first_by_leaf.items():
            if funnel is not None:
                funnel["tree_leaves"] += 1
            count = len(indices)
            if count < config.min_discovery_events:
                continue
            if funnel is not None:
                funnel["minimum_event_support"] += 1
            fades = int(labels[indices].sum())
            rate = fades / count
            matched_indices = indices[np.isfinite(matched_probability[indices])]
            if (
                len(matched_indices) < config.min_discovery_events
                or len(matched_indices) / count < config.min_matched_signal_fraction
            ):
                continue
            if funnel is not None:
                funnel["matched_control_coverage"] += 1
            matched_rate = float(matched_probability[matched_indices].mean())
            matched_signal_rate = float(labels[matched_indices].mean())
            lift = matched_signal_rate / matched_rate if matched_rate > 0.0 else math.inf
            if rate < config.min_discovery_fade_rate:
                continue
            if funnel is not None:
                funnel["minimum_fade_rate"] += 1
            if lift < config.min_discovery_lift:
                continue
            if funnel is not None:
                funnel["minimum_lift"] += 1
            lower, edge_lower, _, inference_blocks = _cluster_bootstrap(
                frame=frame,
                indices=matched_indices,
                labels=labels,
                matched_probability=matched_probability,
                iterations=config.block_bootstrap_iterations,
                seed=config.random_seed + tree_index * 257 + leaf_index,
            )
            if edge_lower <= 0.0:
                continue
            if funnel is not None:
                funnel["positive_cluster_edge"] += 1
            if inference_blocks < config.min_inference_blocks:
                continue
            if funnel is not None:
                funnel["minimum_inference_blocks"] += 1
            rule, rule_text, feature_names = _canonical_rule(tree, leaf_index, model_json)
            if not rule:
                continue
            groups = frozenset(str(value) for value in frame.loc[indices, config.input.group_column])
            candidates.append(
                RuleCandidate(
                    tree_index=tree_index,
                    leaf_index=leaf_index,
                    rule=rule,
                    rule_text=rule_text,
                    feature_names=feature_names,
                    discovery_indices=indices,
                    discovery_groups=groups,
                    discovery_count=count,
                    discovery_fades=fades,
                    discovery_rate=rate,
                    discovery_lift=lift,
                    discovery_lower=lower,
                    discovery_matched_rate=matched_rate,
                    discovery_edge_lower=edge_lower,
                    discovery_matched_count=len(matched_indices),
                    discovery_inference_blocks=inference_blocks,
                )
            )
            if funnel is not None:
                funnel["generated_rule"] += 1
    return candidates


def select_distinct_candidates(
    candidates: list[RuleCandidate], config: ArchetypeDiscoveryConfig
) -> list[RuleCandidate]:
    ranked = sorted(
        candidates,
        key=lambda item: (item.discovery_lower, item.discovery_rate, item.discovery_count),
        reverse=True,
    )
    selected: list[RuleCandidate] = []
    covered: set[str] = set()
    for candidate in ranked:
        membership = candidate.reference_groups or candidate.discovery_groups
        distinct = True
        for existing in selected:
            existing_membership = existing.reference_groups or existing.discovery_groups
            overlap = _membership_jaccard(membership, existing_membership)
            if overlap > config.max_membership_jaccard:
                distinct = False
                break
        unique_fraction = len(membership - covered) / len(membership) if membership else 0.0
        if unique_fraction < config.min_unique_event_fraction:
            distinct = False
        if distinct:
            candidate.discovery_unique_fraction = unique_fraction
            selected.append(candidate)
            covered.update(membership)
        if config.max_categories is not None and len(selected) >= config.max_categories:
            break
    return selected


def _consensus_candidates(
    candidates: list[RuleCandidate], config: ArchetypeDiscoveryConfig
) -> list[RuleCandidate]:
    ranked = sorted(
        candidates,
        key=lambda item: (item.discovery_lower, item.discovery_rate, item.discovery_count),
        reverse=True,
    )
    clusters: list[list[RuleCandidate]] = []
    for candidate in ranked:
        for cluster in clusters:
            if _membership_jaccard(
                candidate.reference_groups, cluster[0].reference_groups
            ) >= config.consensus_membership_jaccard:
                cluster.append(candidate)
                break
        else:
            clusters.append([candidate])
    generator_total = len(config.effective_generators)
    origin_total = len(config.rolling_origin_fractions)
    consensus: list[RuleCandidate] = []
    for cluster in clusters:
        origins = {item.origin_id for item in cluster}
        generators = {(item.generator_seed, item.generator_depth) for item in cluster}
        origin_fraction = len(origins) / origin_total
        generator_fraction = len(generators) / generator_total
        if (
            origin_fraction < config.min_origin_support_fraction
            or generator_fraction < config.min_generator_support_fraction
        ):
            continue
        representative = cluster[0]
        representative.origin_support_count = len(origins)
        representative.origin_support_fraction = origin_fraction
        representative.generator_support_count = len(generators)
        representative.generator_support_fraction = generator_fraction
        consensus.append(representative)
    return consensus


def _benjamini_yekutieli(p_values: list[float]) -> list[float]:
    count = len(p_values)
    if count == 0:
        return []
    order = np.argsort(np.asarray(p_values, dtype=float))
    adjusted = np.ones(count, dtype=float)
    running = 1.0
    dependence_factor = sum(1.0 / rank for rank in range(1, count + 1))
    for reverse_rank in range(count - 1, -1, -1):
        index = int(order[reverse_rank])
        rank = reverse_rank + 1
        running = min(running, p_values[index] * count * dependence_factor / rank)
        adjusted[index] = min(1.0, running)
    return adjusted.tolist()


def evaluate_candidates(
    *,
    candidates: list[RuleCandidate],
    prepared: PreparedArchetypeData,
    config: ArchetypeDiscoveryConfig,
) -> None:
    if not candidates:
        return
    frame = prepared.verification
    labels_all = frame[config.input.label_column].to_numpy(dtype=np.int8)
    matched_probability = _matched_probabilities(frame, config)
    evaluated: list[RuleCandidate] = []
    for candidate in candidates:
        indices = _first_matching_rule_rows(
            frame,
            prepared.x_verification,
            candidate.rule,
            group_column=config.input.group_column,
        )
        labels = frame.loc[indices, config.input.label_column].to_numpy(dtype=np.int8)
        count = len(labels)
        fades = int(labels.sum())
        candidate.verification_indices = indices
        candidate.verification_count = count
        candidate.verification_fades = fades
        candidate.verification_rate = fades / count if count else 0.0
        matched_indices = indices[np.isfinite(matched_probability[indices])]
        if (
            count
            and len(matched_indices) >= config.min_verification_events
            and len(matched_indices) / count >= config.min_matched_signal_fraction
        ):
            candidate.verification_matched_count = len(matched_indices)
            candidate.verification_matched_rate = float(matched_probability[matched_indices].mean())
            matched_signal_rate = float(labels_all[matched_indices].mean())
            candidate.verification_lift = (
                matched_signal_rate / candidate.verification_matched_rate
                if candidate.verification_matched_rate > 0.0
                else 0.0
            )
            (
                candidate.verification_lower,
                candidate.verification_edge_lower,
                candidate.verification_p,
                candidate.verification_inference_blocks,
            ) = _cluster_bootstrap(
                frame=frame,
                indices=matched_indices,
                labels=labels_all,
                matched_probability=matched_probability,
                iterations=config.block_bootstrap_iterations,
                seed=(
                    config.random_seed
                    + 1_000_003
                    + candidate.generator_seed * 17
                    + candidate.tree_index * 257
                    + candidate.leaf_index
                ),
            )
            times = pd.to_datetime(frame.loc[matched_indices, "__snapshot_ms"], unit="ms", utc=True)
            periods = times.dt.tz_localize(None).dt.to_period(config.stability_frequency)
            summary = pd.DataFrame(
                {
                    "period": periods.astype(str).to_numpy(),
                    "label": labels_all[matched_indices],
                    "matched": matched_probability[matched_indices],
                }
            ).groupby("period").agg(
                count=("label", "count"),
                mean=("label", "mean"),
                matched=("matched", "mean"),
            )
            eligible = summary[summary["count"] >= config.min_events_per_stability_period]
            candidate.stability_periods = len(eligible)
            candidate.positive_period_fraction = (
                float((eligible["mean"] > eligible["matched"]).mean())
                if len(eligible)
                else 0.0
            )
        evaluated.append(candidate)
    p_values = [candidate.verification_p for candidate in evaluated]
    for candidate, q_value in zip(evaluated, _benjamini_yekutieli(p_values), strict=True):
        candidate.verification_q = q_value


def _is_verified(
    candidate: RuleCandidate,
    *,
    config: ArchetypeDiscoveryConfig,
) -> bool:
    return (
        candidate.verification_count >= config.min_verification_events
        and candidate.verification_matched_count >= config.min_verification_events
        and candidate.verification_inference_blocks >= config.min_inference_blocks
        and candidate.verification_rate >= config.min_verification_fade_rate
        and candidate.verification_lift >= config.min_verification_lift
        and candidate.verification_edge_lower > 0.0
        and candidate.verification_q <= config.fdr_alpha
        and candidate.stability_periods > 0
        and candidate.positive_period_fraction >= config.min_positive_stability_fraction
    )


def calendar_block_permute_labels(
    frame: pd.DataFrame, *, config: ArchetypeDiscoveryConfig, seed: int
) -> tuple[np.ndarray, float]:
    rng = np.random.default_rng(seed)
    group_column = config.input.group_column
    label_column = config.input.label_column
    shuffled = frame[label_column].to_numpy(dtype=np.int8).copy()
    grouped = frame.groupby(group_column, sort=False)
    group_positions = {
        str(group): np.asarray(indices, dtype=np.int64)
        for group, indices in grouped.indices.items()
    }
    groups = grouped.agg(snapshot_ms=("__snapshot_ms", "min"))
    groups.index = groups.index.astype(str)
    groups["row_count"] = [len(group_positions[group]) for group in groups.index]
    groups["calendar_month"] = pd.to_datetime(
        groups["snapshot_ms"], unit="ms", utc=True
    ).dt.strftime("%Y-%m")
    moved_rows = 0
    for _, stratum in groups.groupby(["calendar_month", "row_count"], sort=False):
        recipient_groups = stratum.index.to_numpy(dtype=str)
        if len(recipient_groups) < 2:
            continue
        order = rng.permutation(recipient_groups)
        donor_groups = np.roll(order, 1)
        for recipient, donor in zip(order, donor_groups, strict=True):
            recipient_positions = group_positions[recipient]
            donor_positions = group_positions[donor]
            shuffled[recipient_positions] = frame.iloc[donor_positions][label_column].to_numpy(
                dtype=np.int8
            )
            moved_rows += len(recipient_positions)
    fraction = moved_rows / len(frame) if len(frame) else 0.0
    return shuffled, fraction


def _safe_auc(
    y_true: np.ndarray, probability: np.ndarray, *, sample_weight: np.ndarray
) -> float:
    return (
        float(roc_auc_score(y_true, probability, sample_weight=sample_weight))
        if len(np.unique(y_true)) == 2
        else 0.5
    )


def _empirical_upper_tail_p(observed: float, null_values: list[float]) -> float:
    if not null_values:
        return 1.0
    return float(
        (1 + np.count_nonzero(np.asarray(null_values, dtype=float) >= observed))
        / (len(null_values) + 1)
    )


def _generate_candidate_pool(
    *,
    prepared: PreparedArchetypeData,
    config: ArchetypeDiscoveryConfig,
    labels: np.ndarray | None = None,
    reusable_model: CatBoostClassifier | None = None,
    reusable_model_json: dict[str, Any] | None = None,
    reusable_generator: ArchetypeGeneratorSpec | None = None,
) -> tuple[list[RuleCandidate], int, Counter[str]]:
    full_labels = (
        prepared.discovery[config.input.label_column].to_numpy(dtype=np.int8)
        if labels is None
        else np.asarray(labels, dtype=np.int8)
    )
    pool: list[RuleCandidate] = []
    funnel: Counter[str] = Counter()
    fit_count = 0
    for fraction in config.rolling_origin_fractions:
        positions = _origin_positions(prepared, config, fraction)
        origin = _subset_prepared(prepared, positions)
        origin_labels = full_labels[positions]
        if len(np.unique(origin_labels)) != 2:
            raise ArchetypeDiscoveryError(
                f"rolling origin {fraction:.3f} does not contain both target classes"
            )
        origin_id = f"origin_{fraction:.3f}"
        origin_matched_probability = _matched_probabilities(
            origin.discovery, config, labels=origin_labels
        )
        for generator in config.effective_generators:
            can_reuse = (
                fraction == 1.0
                and generator == reusable_generator
                and reusable_model is not None
                and reusable_model_json is not None
            )
            if can_reuse:
                model = reusable_model
                model_json = reusable_model_json
            else:
                model = fit_rule_generator(
                    origin,
                    config,
                    labels=origin_labels,
                    random_seed=generator.random_seed,
                    depth=generator.depth,
                )
                model_json = export_model_json(model)
                fit_count += 1
            generated = discover_candidates(
                model=model,
                model_json=model_json,
                prepared=origin,
                config=config,
                discovery_labels=origin_labels,
                matched_probability=origin_matched_probability,
                funnel=funnel,
            )
            reference_rows_by_tree: dict[int, dict[int, np.ndarray]] = {}
            if generated:
                reference_leaf_matrix = np.asarray(
                    model.calc_leaf_indexes(prepared.x_discovery)
                )
                for tree_index in {candidate.tree_index for candidate in generated}:
                    leaf_count = len(
                        model_json["oblivious_trees"][tree_index]["leaf_values"]
                    )
                    reference_rows_by_tree[tree_index] = _first_leaf_rows(
                        prepared.discovery,
                        reference_leaf_matrix[:, tree_index],
                        group_column=config.input.group_column,
                        leaf_count=leaf_count,
                    )
            for candidate in generated:
                candidate.generator_seed = generator.random_seed
                candidate.generator_depth = generator.depth
                candidate.origin_fraction = fraction
                candidate.origin_id = origin_id
                candidate.discovery_indices = positions[candidate.discovery_indices]
                reference_indices = reference_rows_by_tree[candidate.tree_index].get(
                    candidate.leaf_index,
                    np.array([], dtype=np.int64),
                )
                candidate.reference_indices = reference_indices
                candidate.reference_groups = frozenset(
                    str(value)
                    for value in prepared.discovery.loc[
                        reference_indices, config.input.group_column
                    ]
                )
            pool.extend(generated)
    return pool, fit_count, funnel


def _build_category_outputs(
    *,
    candidates: list[RuleCandidate],
    prepared: PreparedArchetypeData,
    config: ArchetypeDiscoveryConfig,
    controls_passed: bool,
) -> tuple[list[ArchetypeCategoryRow], pd.DataFrame]:
    discovery_events, discovery_blind = _blind_rate(prepared.discovery, config)
    verification_events, verification_blind = _blind_rate(prepared.verification, config)
    del discovery_events, verification_events
    rows: list[ArchetypeCategoryRow] = []
    assignments: list[dict[str, Any]] = []
    contract = config.input
    for rank, candidate in enumerate(candidates, start=1):
        category_id = f"fade_archetype_{rank:03d}"
        passed = _is_verified(candidate, config=config)
        status = (
            "REJECTED"
            if not passed
            else "CONTROL_FAILED"
            if not controls_passed
            else "PRISTINE_VERIFIED"
            if config.evidence_mode == "pristine_holdout"
            else "DEVELOPMENT_REPLICATED"
        )
        rows.append(
            ArchetypeCategoryRow(
                category_id=category_id,
                status=status,
                evidence_mode=config.evidence_mode,
                protocol_freeze_id=config.protocol_freeze_id,
                tree_index=candidate.tree_index,
                leaf_index=candidate.leaf_index,
                generator_seed=candidate.generator_seed,
                generator_depth=candidate.generator_depth,
                origin_fraction=candidate.origin_fraction,
                origin_support_count=candidate.origin_support_count,
                origin_support_fraction=candidate.origin_support_fraction,
                generator_support_count=candidate.generator_support_count,
                generator_support_fraction=candidate.generator_support_fraction,
                rule_text=candidate.rule_text,
                rule_json=json.dumps(candidate.rule, ensure_ascii=False, separators=(",", ":")),
                feature_names=";".join(candidate.feature_names),
                discovery_event_count=candidate.discovery_count,
                discovery_fade_count=candidate.discovery_fades,
                discovery_matched_event_count=candidate.discovery_matched_count,
                discovery_unique_event_fraction=candidate.discovery_unique_fraction,
                discovery_inference_block_count=candidate.discovery_inference_blocks,
                discovery_fade_rate=candidate.discovery_rate,
                discovery_global_blind_rate=discovery_blind,
                discovery_matched_blind_rate=candidate.discovery_matched_rate,
                discovery_lift=candidate.discovery_lift,
                discovery_cluster_lower_95=candidate.discovery_lower,
                discovery_cluster_edge_lower_95=candidate.discovery_edge_lower,
                verification_event_count=candidate.verification_count,
                verification_fade_count=candidate.verification_fades,
                verification_matched_event_count=candidate.verification_matched_count,
                verification_inference_block_count=candidate.verification_inference_blocks,
                verification_fade_rate=candidate.verification_rate,
                verification_global_blind_rate=verification_blind,
                verification_matched_blind_rate=candidate.verification_matched_rate,
                verification_lift=candidate.verification_lift,
                verification_cluster_lower_95=candidate.verification_lower,
                verification_cluster_edge_lower_95=candidate.verification_edge_lower,
                verification_p_value=candidate.verification_p,
                verification_q_value=candidate.verification_q,
                verification_stability_periods=candidate.stability_periods,
                verification_positive_period_fraction=candidate.positive_period_fraction,
                first_signal_policy=FIRST_SIGNAL_POLICY,
                temporal_contract=ARCHETYPE_TEMPORAL_CONTRACT,
            )
        )
        if not passed or not controls_passed:
            continue
        for split_name, frame, indices in (
            ("discovery", prepared.discovery, candidate.discovery_indices),
            (
                "verification",
                prepared.verification,
                candidate.verification_indices
                if candidate.verification_indices is not None
                else np.array([], dtype=np.int64),
            ),
        ):
            for index in indices:
                row = frame.iloc[int(index)]
                assignments.append(
                    {
                        "category_id": category_id,
                        "split": split_name,
                        "group": str(row[contract.group_column]),
                        "symbol": str(row[contract.symbol_column]),
                        "snapshot_time_ms": int(row["__snapshot_ms"]),
                        "label": int(row[contract.label_column]),
                    }
                )
    assignment_frame = pd.DataFrame(
        assignments,
        columns=["category_id", "split", "group", "symbol", "snapshot_time_ms", "label"],
    )
    return rows, assignment_frame


def _coverage_outputs(
    *,
    candidates: list[RuleCandidate],
    prepared: PreparedArchetypeData,
    config: ArchetypeDiscoveryConfig,
    generated_candidate_count: int,
    search_truncated: bool,
    controls_passed: bool,
) -> tuple[list[ArchetypeCoverageRow], pd.DataFrame]:
    accepted = (
        [candidate for candidate in candidates if _is_verified(candidate, config=config)]
        if controls_passed
        else []
    )
    coverage_rows: list[ArchetypeCoverageRow] = []
    unclassified_assignments: list[dict[str, Any]] = []
    for split_name, frame, matrix in (
        ("discovery", prepared.discovery, prepared.x_discovery),
        ("verification", prepared.verification, prepared.x_verification),
    ):
        group_column = config.input.group_column
        label_column = config.input.label_column
        first = frame.drop_duplicates(group_column, keep="first")
        all_groups = first[group_column].astype(str).to_numpy()
        labels_by_group = dict(
            zip(
                all_groups,
                (int(value) for value in first[label_column].to_numpy(dtype=np.int8)),
                strict=True,
            )
        )
        hit_counts = {group: 0 for group in all_groups}
        for candidate in accepted:
            indices = _first_matching_rule_rows(
                frame, matrix, candidate.rule, group_column=group_column
            )
            for value in frame.loc[indices, group_column].astype(str):
                hit_counts[value] += 1
        covered = {group for group, count in hit_counts.items() if count > 0}
        overlapping = sum(count > 1 for count in hit_counts.values())
        unclassified = [group for group in all_groups if group not in covered]
        fades = sum(labels_by_group.values())
        covered_fades = sum(labels_by_group[group] for group in covered)
        unclassified_fades = sum(labels_by_group[group] for group in unclassified)
        total = len(all_groups)
        coverage_rows.append(
            ArchetypeCoverageRow(
                split=split_name,
                total_event_count=total,
                first_signal_fade_event_count=fades,
                covered_event_count=len(covered),
                covered_first_signal_fade_event_count=covered_fades,
                overlapping_event_count=overlapping,
                unclassified_event_count=len(unclassified),
                covered_fraction=len(covered) / total if total else 0.0,
                covered_first_signal_fade_fraction=covered_fades / fades if fades else 0.0,
                unclassified_first_signal_fade_rate=(
                    unclassified_fades / len(unclassified) if unclassified else 0.0
                ),
                accepted_category_count=len(accepted),
                generated_candidate_count=generated_candidate_count,
                distinct_candidate_count=len(candidates),
                registered_generator_count=len(config.effective_generators),
                rolling_origin_count=len(config.rolling_origin_fractions),
                search_truncated=search_truncated,
                controls_passed=controls_passed,
                claim_scope=(
                    "registered causal features; registered CatBoost generators and rolling origins; "
                    "minimum support/effect gates; predictive phenotypes, not causal mechanisms"
                ),
            )
        )
        first_by_group = first.set_index(first[group_column].astype(str), drop=False)
        for group in unclassified:
            row = first_by_group.loc[group]
            unclassified_assignments.append(
                {
                    "category_id": "unclassified",
                    "split": split_name,
                    "group": group,
                    "symbol": str(row[config.input.symbol_column]),
                    "snapshot_time_ms": int(row["__snapshot_ms"]),
                    "label": int(row[label_column]),
                }
            )
    return coverage_rows, pd.DataFrame(
        unclassified_assignments,
        columns=["category_id", "split", "group", "symbol", "snapshot_time_ms", "label"],
    )


def build_archetype_discovery(
    frame: pd.DataFrame, config: ArchetypeDiscoveryConfig
) -> ArchetypeBuildResult:
    prepared = prepare_archetype_data(frame, config)
    primary_generator = config.effective_generators[0]
    model = fit_rule_generator(
        prepared,
        config,
        random_seed=primary_generator.random_seed,
        depth=primary_generator.depth,
    )
    model_json = export_model_json(model)
    candidates, fit_count, candidate_funnel = _generate_candidate_pool(
        prepared=prepared,
        config=config,
        reusable_model=model,
        reusable_model_json=model_json,
        reusable_generator=primary_generator,
    )
    consensus = _consensus_candidates(candidates, config)
    selected = select_distinct_candidates(consensus, config)
    search_truncated = (
        config.max_categories is not None
        and len(selected) >= config.max_categories
        and len(consensus) > len(selected)
    )
    evaluate_candidates(
        candidates=selected, prepared=prepared, config=config
    )
    y_verification = prepared.verification[config.input.label_column].to_numpy(dtype=np.int8)
    verification_probability = model.predict_proba(prepared.x_verification)[:, 1]
    verification_weight = _group_inverse_weights(
        prepared.verification, config.input.group_column
    )
    verification_auc = _safe_auc(
        y_verification, verification_probability, sample_weight=verification_weight
    )
    discovery_event_count, discovery_blind = _blind_rate(prepared.discovery, config)
    verification_event_count, verification_blind = _blind_rate(prepared.verification, config)
    real_verified = sum(_is_verified(item, config=config) for item in selected)
    shuffled_results: list[tuple[float, int, float]] = []
    controls = [
        ArchetypeControlRow(
            control_name="blind",
            random_seed=0,
            discovery_event_count=discovery_event_count,
            verification_event_count=verification_event_count,
            discovery_blind_rate=discovery_blind,
            verification_blind_rate=verification_blind,
            verification_auc=0.5,
            discovery_candidate_count=0,
            distinct_candidate_count=0,
            verified_category_count=0,
            shuffled_row_fraction=0.0,
            notes="First eligible causal snapshot per anomaly; no feature selection.",
        ),
        ArchetypeControlRow(
            control_name="real_labels",
            random_seed=primary_generator.random_seed,
            discovery_event_count=discovery_event_count,
            verification_event_count=verification_event_count,
            discovery_blind_rate=discovery_blind,
            verification_blind_rate=verification_blind,
            verification_auc=verification_auc,
            discovery_candidate_count=len(candidates),
            distinct_candidate_count=len(selected),
            verified_category_count=real_verified,
            shuffled_row_fraction=0.0,
            notes=(
                "Rules generated on discovery labels and frozen before the later interval; "
                "candidate consensus spans registered rolling origins and generators; "
                "AUC uses inverse-group weights so each anomaly has unit total weight."
            ),
        ),
    ]
    for seed in config.shuffled_seeds:
        shuffled_labels, shuffled_fraction = calendar_block_permute_labels(
            prepared.discovery, config=config, seed=seed
        )
        shuffled_generator = config.effective_generators[0]
        shuffled_model = fit_rule_generator(
            prepared,
            config,
            labels=shuffled_labels,
            random_seed=shuffled_generator.random_seed,
            depth=shuffled_generator.depth,
        )
        fit_count += 1
        if real_verified:
            shuffled_json = export_model_json(shuffled_model)
            shuffled_candidates, shuffled_fit_count, _ = _generate_candidate_pool(
                prepared=prepared,
                config=config,
                labels=shuffled_labels,
                reusable_model=shuffled_model,
                reusable_model_json=shuffled_json,
                reusable_generator=shuffled_generator,
            )
            fit_count += shuffled_fit_count
            shuffled_consensus = _consensus_candidates(shuffled_candidates, config)
            shuffled_selected = select_distinct_candidates(shuffled_consensus, config)
            evaluate_candidates(
                candidates=shuffled_selected,
                prepared=prepared,
                config=config,
            )
            shuffled_verified = sum(
                _is_verified(item, config=config) for item in shuffled_selected
            )
        else:
            shuffled_candidates = []
            shuffled_selected = []
            shuffled_verified = 0
        shuffled_probability = shuffled_model.predict_proba(prepared.x_verification)[:, 1]
        shuffled_auc = _safe_auc(
            y_verification,
            shuffled_probability,
            sample_weight=verification_weight,
        )
        shuffled_results.append((shuffled_fraction, shuffled_verified, shuffled_auc))
        controls.append(
            ArchetypeControlRow(
                control_name="calendar_block_shuffled_labels",
                random_seed=seed,
                discovery_event_count=discovery_event_count,
                verification_event_count=verification_event_count,
                discovery_blind_rate=discovery_blind,
                verification_blind_rate=verification_blind,
                verification_auc=shuffled_auc,
                discovery_candidate_count=len(shuffled_candidates),
                distinct_candidate_count=len(shuffled_selected),
                verified_category_count=shuffled_verified,
                shuffled_row_fraction=shuffled_fraction,
                notes=(
                    "Complete within-anomaly label paths are transplanted between groups with the same "
                    "calendar month and row count; rules are evaluated on real later outcomes. "
                    + (
                        "The full null rule search matches the real search budget."
                        if real_verified
                        else "The null rule search is skipped because the real search produced no verified category."
                    )
                ),
            )
        )
    sufficient_shuffle = all(
        fraction >= config.min_shuffled_row_fraction
        for fraction, _, _ in shuffled_results
    )
    null_aucs = [auc for _, _, auc in shuffled_results]
    null_category_counts = [float(verified) for _, verified, _ in shuffled_results]
    auc_control_p = _empirical_upper_tail_p(verification_auc, null_aucs)
    category_control_p = _empirical_upper_tail_p(
        float(real_verified), null_category_counts
    )
    controls_passed = (
        sufficient_shuffle
        and auc_control_p <= config.control_empirical_alpha
        and (
            real_verified == 0
            or category_control_p <= config.control_empirical_alpha
        )
    )
    controls[1] = replace(
        controls[1],
        verified_category_count=real_verified if controls_passed else 0,
        notes=(
            controls[1].notes
            + (
                f" Empirical AUC-null p={auc_control_p:.6g}; "
                f"category-count-null p={category_control_p:.6g}. "
                "All registered shuffled controls passed."
                if controls_passed
                else f" Empirical AUC-null p={auc_control_p:.6g}; "
                f"category-count-null p={category_control_p:.6g}. "
                "Registered shuffled controls failed; real candidates are blocked."
            )
        ),
    )
    candidate_funnel["origin_generator_consensus"] = len(consensus)
    candidate_funnel["distinct_marginal_coverage"] = len(selected)
    candidate_funnel["later_verification_gate"] = real_verified
    funnel_notes = {
        "tree_leaves": "all non-empty leaf memberships across registered real-label origin-generator fits",
        "minimum_event_support": "discovery event count meets the registered minimum",
        "matched_control_coverage": "matched blind exists for the registered fraction of signal events",
        "minimum_fade_rate": "absolute discovery fade rate meets the registered high-probability floor",
        "minimum_lift": "matched discovery lift meets the registered floor",
        "positive_cluster_edge": "weekly block-bootstrap lower edge is positive",
        "minimum_inference_blocks": "candidate spans the registered minimum ISO-week blocks",
        "generated_rule": "rule passed every per-fit discovery gate",
        "origin_generator_consensus": "membership-equivalent rule recurs across registered origins and generators",
        "distinct_marginal_coverage": "rule adds sufficient events not covered by earlier selected rules",
        "later_verification_gate": "frozen rule passes later support, lift, BY-FDR, edge, and stability gates",
    }
    candidate_funnel_rows = [
        ArchetypeCandidateFunnelRow(
            stage=stage,
            candidate_count=int(candidate_funnel.get(stage, 0)),
            notes=funnel_notes[stage],
        )
        for stage in funnel_notes
    ]
    category_rows, assignments = _build_category_outputs(
        candidates=selected,
        prepared=prepared,
        config=config,
        controls_passed=controls_passed,
    )
    coverage_rows, unclassified = _coverage_outputs(
        candidates=selected,
        prepared=prepared,
        config=config,
        generated_candidate_count=len(candidates),
        search_truncated=search_truncated,
        controls_passed=controls_passed,
    )
    assignments = pd.concat([assignments, unclassified], ignore_index=True)
    return ArchetypeBuildResult(
        category_rows=category_rows,
        control_rows=controls,
        coverage_rows=coverage_rows,
        assignments=assignments,
        model=model,
        model_json=model_json,
        prepared=prepared,
        discovery_candidate_count=len(candidates),
        distinct_candidate_count=len(selected),
        verification_auc=verification_auc,
        generator_fit_count=fit_count + 1,
        search_truncated=search_truncated,
        controls_passed=controls_passed,
        auc_control_empirical_p=auc_control_p,
        category_count_control_empirical_p=category_control_p,
        candidate_funnel_rows=candidate_funnel_rows,
    )
