from __future__ import annotations

import json
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score

from anomaly_science.archetypes.config import ArchetypeDiscoveryConfig
from anomaly_science.contracts.archetypes import ArchetypeCategoryRow, ArchetypeControlRow
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


@dataclass(slots=True)
class ArchetypeBuildResult:
    category_rows: list[ArchetypeCategoryRow]
    control_rows: list[ArchetypeControlRow]
    assignments: pd.DataFrame
    model: CatBoostClassifier
    model_json: dict[str, Any]
    prepared: PreparedArchetypeData
    discovery_candidate_count: int
    distinct_candidate_count: int
    verification_auc: float


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
    snapshot, cutoff, future_start, resolution = _time_arrays(work, config)
    work["__snapshot_ms"] = snapshot
    work["__feature_cutoff_ms"] = cutoff
    work["__future_start_ms"] = future_start
    work["__resolution_ms"] = resolution
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
        work = work[np.isfinite(work["__resolution_ms"])].copy()
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
        depth=config.depth,
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
) -> list[RuleCandidate]:
    frame = prepared.discovery
    labels = (
        frame[config.input.label_column].to_numpy(dtype=np.int8)
        if discovery_labels is None
        else np.asarray(discovery_labels, dtype=np.int8)
    )
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
            count = len(indices)
            if count < config.min_discovery_events:
                continue
            fades = int(labels[indices].sum())
            rate = fades / count
            matched_indices = indices[np.isfinite(matched_probability[indices])]
            if (
                len(matched_indices) < config.min_discovery_events
                or len(matched_indices) / count < config.min_matched_signal_fraction
            ):
                continue
            matched_rate = float(matched_probability[matched_indices].mean())
            matched_signal_rate = float(labels[matched_indices].mean())
            lift = matched_signal_rate / matched_rate if matched_rate > 0.0 else math.inf
            if rate < config.min_discovery_fade_rate or lift < config.min_discovery_lift:
                continue
            lower, edge_lower, _, inference_blocks = _cluster_bootstrap(
                frame=frame,
                indices=matched_indices,
                labels=labels,
                matched_probability=matched_probability,
                iterations=config.block_bootstrap_iterations,
                seed=config.random_seed + tree_index * 257 + leaf_index,
            )
            if edge_lower <= 0.0 or inference_blocks < config.min_inference_blocks:
                continue
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
        distinct = True
        for existing in selected:
            union = len(candidate.discovery_groups | existing.discovery_groups)
            overlap = len(candidate.discovery_groups & existing.discovery_groups) / union if union else 1.0
            if overlap > config.max_membership_jaccard:
                distinct = False
                break
        unique_fraction = len(candidate.discovery_groups - covered) / len(candidate.discovery_groups)
        if unique_fraction < config.min_unique_event_fraction:
            distinct = False
        if distinct:
            candidate.discovery_unique_fraction = unique_fraction
            selected.append(candidate)
            covered.update(candidate.discovery_groups)
        if len(selected) >= config.max_categories:
            break
    return selected


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
    model: CatBoostClassifier,
    prepared: PreparedArchetypeData,
    config: ArchetypeDiscoveryConfig,
) -> None:
    if not candidates:
        return
    frame = prepared.verification
    labels_all = frame[config.input.label_column].to_numpy(dtype=np.int8)
    matched_probability = _matched_probabilities(frame, config)
    leaf_matrix = np.asarray(model.calc_leaf_indexes(prepared.x_verification))
    by_tree: dict[int, list[RuleCandidate]] = {}
    for candidate in candidates:
        by_tree.setdefault(candidate.tree_index, []).append(candidate)
    evaluated: list[RuleCandidate] = []
    for tree_index, tree_candidates in by_tree.items():
        leaf_count = int(2 ** config.depth)
        first_by_leaf = _first_leaf_rows(
            frame,
            leaf_matrix[:, tree_index],
            group_column=config.input.group_column,
            leaf_count=leaf_count,
        )
        for candidate in tree_candidates:
            indices = first_by_leaf.get(candidate.leaf_index, np.array([], dtype=np.int64))
            labels = frame.loc[indices, config.input.label_column].to_numpy(dtype=np.int8)
            count = len(labels)
            fades = int(labels.sum())
            rate = fades / count if count else 0.0
            candidate.verification_indices = indices
            candidate.verification_count = count
            candidate.verification_fades = fades
            candidate.verification_rate = rate
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
                    seed=config.random_seed + 1_000_003 + tree_index * 257 + candidate.leaf_index,
                )
                times = pd.to_datetime(frame.loc[matched_indices, "__snapshot_ms"], unit="ms", utc=True)
                periods = times.dt.tz_localize(None).dt.to_period(config.stability_frequency)
                summary = pd.DataFrame(
                    {
                        "period": periods.astype(str).to_numpy(),
                        "label": labels_all[matched_indices],
                        "matched": matched_probability[matched_indices],
                    }
                ).groupby("period").agg(count=("label", "count"), mean=("label", "mean"), matched=("matched", "mean"))
                eligible = summary[summary["count"] >= config.min_events_per_stability_period]
                candidate.stability_periods = len(eligible)
                candidate.positive_period_fraction = (
                    float((eligible["mean"] > eligible["matched"]).mean()) if len(eligible) else 0.0
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
    labels = frame[config.input.label_column].to_numpy(dtype=np.int8)
    shuffled = labels.copy()
    strata = pd.DataFrame(index=frame.index)
    strata["calendar_month"] = pd.to_datetime(
        frame["__snapshot_ms"], unit="ms", utc=True
    ).dt.strftime("%Y-%m")
    stratum_key = pd.util.hash_pandas_object(strata, index=False).to_numpy(dtype=np.uint64)
    moved_rows = 0
    for key in np.unique(stratum_key):
        positions = np.flatnonzero(stratum_key == key)
        if len(positions) < 2:
            continue
        order = rng.permutation(positions)
        donors = np.roll(order, 1)
        shuffled[order] = labels[donors]
        moved_rows += len(positions)
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


def _build_category_outputs(
    *,
    candidates: list[RuleCandidate],
    prepared: PreparedArchetypeData,
    config: ArchetypeDiscoveryConfig,
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
        if not passed:
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


def build_archetype_discovery(
    frame: pd.DataFrame, config: ArchetypeDiscoveryConfig
) -> ArchetypeBuildResult:
    prepared = prepare_archetype_data(frame, config)
    model = fit_rule_generator(prepared, config)
    model_json = export_model_json(model)
    candidates = discover_candidates(
        model=model, model_json=model_json, prepared=prepared, config=config
    )
    selected = select_distinct_candidates(candidates, config)
    evaluate_candidates(
        candidates=selected, model=model, prepared=prepared, config=config
    )
    category_rows, assignments = _build_category_outputs(
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
    real_verified = sum(row.status != "REJECTED" for row in category_rows)
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
            random_seed=config.random_seed,
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
                "AUC uses inverse-group weights so each anomaly has unit total weight."
            ),
        ),
    ]
    for seed in config.shuffled_seeds:
        shuffled_labels, shuffled_fraction = calendar_block_permute_labels(
            prepared.discovery, config=config, seed=seed
        )
        shuffled_model = fit_rule_generator(
            prepared, config, labels=shuffled_labels, random_seed=seed
        )
        shuffled_json = export_model_json(shuffled_model)
        shuffled_candidates = discover_candidates(
            model=shuffled_model,
            model_json=shuffled_json,
            prepared=prepared,
            config=config,
            discovery_labels=shuffled_labels,
        )
        shuffled_selected = select_distinct_candidates(shuffled_candidates, config)
        evaluate_candidates(
            candidates=shuffled_selected,
            model=shuffled_model,
            prepared=prepared,
            config=config,
        )
        shuffled_verified = sum(
            _is_verified(item, config=config)
            for item in shuffled_selected
        )
        shuffled_probability = shuffled_model.predict_proba(prepared.x_verification)[:, 1]
        controls.append(
            ArchetypeControlRow(
                control_name="calendar_block_shuffled_labels",
                random_seed=seed,
                discovery_event_count=discovery_event_count,
                verification_event_count=verification_event_count,
                discovery_blind_rate=discovery_blind,
                verification_blind_rate=verification_blind,
                verification_auc=_safe_auc(
                    y_verification,
                    shuffled_probability,
                    sample_weight=verification_weight,
                ),
                discovery_candidate_count=len(shuffled_candidates),
                distinct_candidate_count=len(shuffled_selected),
                verified_category_count=shuffled_verified,
                shuffled_row_fraction=shuffled_fraction,
                notes=(
                    "Labels are permuted across anomaly groups inside calendar-month blocks, "
                    "destroying decision-position and feature association while preserving regime base rate; "
                    "rules are evaluated on real later outcomes."
                ),
            )
        )
    return ArchetypeBuildResult(
        category_rows=category_rows,
        control_rows=controls,
        assignments=assignments,
        model=model,
        model_json=model_json,
        prepared=prepared,
        discovery_candidate_count=len(candidates),
        distinct_candidate_count=len(selected),
        verification_auc=verification_auc,
    )
