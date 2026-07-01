from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import tempfile
from typing import Any

from catboost import CatBoostClassifier, CatBoostRegressor
import numpy as np
import pandas as pd

from anomaly_science.phenotypes.config import CrossFittedPhenotypeConfig
from anomaly_science.phenotypes.contracts import (
    FrozenPhenotype,
    PhenotypeDiscoveryResult,
    PreparedPhenotypeData,
    ScreenedLeaf,
)
from anomaly_science.phenotypes.memory import build_phenotype_followup_registry


class PhenotypeDiscoveryError(ValueError):
    """Raised when phenotype discovery cannot preserve its causal protocol."""


def prepare_phenotype_data(
    frame: pd.DataFrame,
    config: CrossFittedPhenotypeConfig,
) -> PreparedPhenotypeData:
    contract = config.input
    required = {
        contract.group_column, contract.split_group_column, contract.symbol_column,
        contract.label_column, contract.snapshot_time_column,
        contract.feature_cutoff_time_column, contract.future_start_time_column,
        contract.resolution_time_column, contract.label_available_column,
        contract.label_schema_column, *contract.required_true_columns,
        *config.numeric_features, *config.categorical_features,
    }
    if contract.row_filter_column:
        required.add(contract.row_filter_column)
    if contract.population_minimum_column:
        required.add(contract.population_minimum_column)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise PhenotypeDiscoveryError(f"phenotype input missing columns: {missing}")
    forbidden = set(config.numeric_features) | set(config.categorical_features)
    forbidden &= set(contract.label_only_columns)
    if forbidden:
        raise PhenotypeDiscoveryError(f"label-only phenotype features: {sorted(forbidden)}")
    work = frame.copy()
    schemas = set(work[contract.label_schema_column].dropna().astype(str).unique())
    if schemas != {contract.required_label_schema_value}:
        raise PhenotypeDiscoveryError(
            f"phenotype label schema mismatch: {sorted(schemas)}"
        )
    if contract.row_filter_column:
        work = work[work[contract.row_filter_column] == contract.row_filter_value].copy()
    if contract.population_minimum_column:
        population = pd.to_numeric(work[contract.population_minimum_column], errors="raise")
        work = work[population >= float(contract.population_minimum_value)].copy()
    for column in contract.required_true_columns:
        work = work[work[column].astype(bool)].copy()
    work = work[work[contract.label_available_column].astype(bool)].copy()
    if work.empty:
        raise PhenotypeDiscoveryError("phenotype population is empty")
    for source, target in (
        (contract.snapshot_time_column, "__snapshot_ms"),
        (contract.feature_cutoff_time_column, "__feature_cutoff_ms"),
        (contract.future_start_time_column, "__future_start_ms"),
        (contract.resolution_time_column, "__resolution_ms"),
    ):
        values = pd.to_numeric(work[source], errors="raise").to_numpy(dtype=np.int64)
        work[target] = values
    if np.any(work["__feature_cutoff_ms"] > work["__snapshot_ms"]):
        raise PhenotypeDiscoveryError("phenotype features exceed snapshot time")
    if np.any(work["__future_start_ms"] <= work["__snapshot_ms"]):
        raise PhenotypeDiscoveryError("phenotype future does not start after snapshot")
    if np.any(work["__resolution_ms"] < work["__future_start_ms"]):
        raise PhenotypeDiscoveryError("phenotype resolution precedes future start")
    labels = pd.to_numeric(work[contract.label_column], errors="raise")
    if not set(labels.unique()).issubset({0, 1}):
        raise PhenotypeDiscoveryError("phenotype target must be binary")
    work[contract.label_column] = labels.astype(np.int8)
    for column in (contract.group_column, contract.split_group_column, contract.symbol_column):
        if work[column].isna().any():
            raise PhenotypeDiscoveryError(f"phenotype identity column {column} is missing")
        work[column] = work[column].astype(str)

    segments = _temporal_segments(work, config)
    search, calibration, verification = segments
    transform = _fit_transform(search, calibration, verification, config)
    return PreparedPhenotypeData(
        search=search,
        calibration=calibration,
        verification=verification,
        x_search=transform[0],
        x_calibration=transform[1],
        x_verification=transform[2],
        imputation_values=transform[3],
        categorical_levels=transform[4],
        model_feature_names=tuple(transform[0].columns),
    )


def _temporal_segments(
    work: pd.DataFrame,
    config: CrossFittedPhenotypeConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    split = config.input.split_group_column
    bounds = work.groupby(split, sort=False).agg(
        first_snapshot=("__snapshot_ms", "min"),
        last_snapshot=("__snapshot_ms", "max"),
        last_resolution=("__resolution_ms", "max"),
    )
    ranges = (
        (config.search_start_ms, config.search_end_ms),
        (config.search_end_ms, config.calibration_end_ms),
        (config.calibration_end_ms, config.verification_end_ms),
    )
    parts: list[pd.DataFrame] = []
    used: set[str] = set()
    for start, end in ranges:
        groups = set(
            bounds.index[
                (bounds["first_snapshot"] >= start)
                & (bounds["last_snapshot"] < end)
                & (bounds["last_resolution"] < end)
            ].astype(str)
        )
        if groups & used:
            raise AssertionError("phenotype split groups overlap temporal segments")
        used |= groups
        part = work[work[split].isin(groups)].sort_values(
            ["__snapshot_ms", config.input.group_column], kind="mergesort"
        ).reset_index(drop=True)
        if part.empty or part[config.input.label_column].nunique() != 2:
            raise PhenotypeDiscoveryError("each phenotype segment must contain both classes")
        parts.append(part)
    return parts[0], parts[1], parts[2]


def _fit_transform(
    search: pd.DataFrame,
    calibration: pd.DataFrame,
    verification: pd.DataFrame,
    config: CrossFittedPhenotypeConfig,
) -> tuple[
    pd.DataFrame, pd.DataFrame, pd.DataFrame,
    dict[str, float], dict[str, tuple[str, ...]],
]:
    output_columns: list[dict[str, np.ndarray]] = [{}, {}, {}]
    imputation: dict[str, float] = {}
    for column in config.numeric_features:
        arrays: list[pd.Series] = []
        for part in (search, calibration, verification):
            values = pd.to_numeric(part[column], errors="coerce").astype(float)
            if np.isinf(values.to_numpy()).any():
                raise PhenotypeDiscoveryError(f"numeric phenotype feature {column} contains infinity")
            arrays.append(values)
        median = float(arrays[0].median())
        if not math.isfinite(median):
            raise PhenotypeDiscoveryError(f"numeric phenotype feature {column} has no search value")
        imputation[column] = median
        for columns, values in zip(output_columns, arrays, strict=True):
            columns[column] = values.fillna(median).to_numpy(dtype=np.float32)
            columns[f"{column}__missing"] = values.isna().to_numpy(dtype=np.float32)
    levels_by_column: dict[str, tuple[str, ...]] = {}
    for column in config.categorical_features:
        raw = [
            part[column].astype("string").fillna("__MISSING__").astype(str)
            for part in (search, calibration, verification)
        ]
        levels = tuple(sorted(raw[0].unique()))
        if not levels:
            raise PhenotypeDiscoveryError(f"categorical phenotype feature {column} has no levels")
        levels_by_column[column] = levels
        for columns, values in zip(output_columns, raw, strict=True):
            for level in levels:
                columns[f"{column}=={level}"] = (values == level).to_numpy(dtype=np.float32)
            columns[f"{column}==__UNKNOWN__"] = (~values.isin(levels)).to_numpy(dtype=np.float32)
    outputs = [pd.DataFrame(columns) for columns in output_columns]
    keep = [name for name in outputs[0].columns if outputs[0][name].nunique() > 1]
    if not keep:
        raise PhenotypeDiscoveryError("all phenotype model features are constant")
    return (
        outputs[0][keep].reset_index(drop=True),
        outputs[1][keep].reset_index(drop=True),
        outputs[2][keep].reset_index(drop=True),
        imputation,
        levels_by_column,
    )


def _cross_fit_slices(
    prepared: PreparedPhenotypeData,
    config: CrossFittedPhenotypeConfig,
) -> tuple[tuple[str, np.ndarray, np.ndarray], ...]:
    frame = prepared.search
    timestamps = pd.to_datetime(frame["__snapshot_ms"], unit="ms", utc=True)
    iso = timestamps.dt.isocalendar()
    weeks = (iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)).to_numpy()
    unique = np.asarray(pd.unique(weeks))
    initial = max(2, int(math.floor(len(unique) * config.initial_train_fraction)))
    remaining = unique[initial:]
    blocks = [block for block in np.array_split(remaining, config.cross_fit_folds) if len(block)]
    if len(blocks) != config.cross_fit_folds:
        raise PhenotypeDiscoveryError("insufficient search ISO weeks for registered folds")
    split_group = config.input.split_group_column
    bounds = frame.groupby(split_group, sort=False).agg(
        last_snapshot=("__snapshot_ms", "max"),
        last_resolution=("__resolution_ms", "max"),
    )
    folds: list[tuple[str, np.ndarray, np.ndarray]] = []
    for index, block in enumerate(blocks, start=1):
        validation_mask = np.isin(weeks, block)
        validation_start = int(frame.loc[validation_mask, "__snapshot_ms"].min())
        validation_end = int(frame.loc[validation_mask, "__snapshot_ms"].max()) + 1
        validation_groups = set(
            bounds.index[
                (bounds["last_snapshot"] >= validation_start)
                & (bounds["last_snapshot"] < validation_end)
            ].astype(str)
        )
        train_groups = set(
            bounds.index[
                (bounds["last_snapshot"] < validation_start)
                & (bounds["last_resolution"] < validation_start)
            ].astype(str)
        )
        train = np.flatnonzero(frame[split_group].isin(train_groups).to_numpy())
        validation = np.flatnonzero(
            validation_mask & frame[split_group].isin(validation_groups).to_numpy()
        )
        if not len(train) or not len(validation):
            raise PhenotypeDiscoveryError(f"phenotype fold {index} is empty")
        folds.append((f"fold_{index}", train, validation))
    return tuple(folds)


def _fit_model(
    x: pd.DataFrame,
    labels: np.ndarray,
    groups: pd.Series,
    *,
    config: CrossFittedPhenotypeConfig,
    seed: int,
    depth: int,
) -> CatBoostClassifier:
    if len(np.unique(labels)) != 2:
        raise PhenotypeDiscoveryError("phenotype fold training requires both classes")
    sizes = groups.groupby(groups).transform("size").to_numpy(dtype=float)
    model = CatBoostClassifier(
        iterations=config.iterations,
        depth=depth,
        learning_rate=config.learning_rate,
        l2_leaf_reg=config.l2_leaf_reg,
        loss_function="Logloss",
        eval_metric="AUC",
        random_seed=seed,
        verbose=False,
        allow_writing_files=False,
        thread_count=config.catboost_thread_count,
    )
    model.fit(x, labels, sample_weight=1.0 / sizes)
    return model


def _fit_risk_surrogate(
    x: pd.DataFrame,
    oof_probability: np.ndarray,
    *,
    config: CrossFittedPhenotypeConfig,
    seed: int,
    depth: int,
) -> CatBoostRegressor:
    model = CatBoostRegressor(
        iterations=config.iterations,
        depth=depth,
        learning_rate=config.learning_rate,
        l2_leaf_reg=config.l2_leaf_reg,
        loss_function="RMSE",
        random_seed=seed,
        verbose=False,
        allow_writing_files=False,
        thread_count=config.catboost_thread_count,
    )
    model.fit(x, oof_probability)
    return model


def _export_model_json(model: CatBoostClassifier) -> dict[str, Any]:
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
    names = {
        int(item["feature_index"]): str(item["feature_id"])
        for item in model_json["features_info"]["float_features"]
    }
    bounds: dict[str, dict[str, float]] = {}
    for depth_index, split in enumerate(tree.get("splits") or []):
        if split.get("split_type") != "FloatFeature":
            raise PhenotypeDiscoveryError("phenotype rules require explicit float features")
        feature = names[int(split["float_feature_index"])]
        border = float(split["border"])
        target = bounds.setdefault(feature, {})
        if leaf_index & (1 << depth_index):
            target["lower"] = max(border, target.get("lower", -math.inf))
        else:
            target["upper"] = min(border, target.get("upper", math.inf))
    conditions: list[dict[str, Any]] = []
    texts: list[str] = []
    for feature in sorted(bounds):
        if "lower" in bounds[feature]:
            value = bounds[feature]["lower"]
            conditions.append({"feature": feature, "operator": ">", "value": value})
            texts.append(f"{feature} > {value:.8g}")
        if "upper" in bounds[feature]:
            value = bounds[feature]["upper"]
            conditions.append({"feature": feature, "operator": "<=", "value": value})
            texts.append(f"{feature} <= {value:.8g}")
    return tuple(conditions), " AND ".join(texts), tuple(sorted(bounds))


def _first_rows_for_leaf(
    frame: pd.DataFrame,
    leaf_values: np.ndarray,
    leaf: int,
    group_column: str,
) -> np.ndarray:
    positions = np.flatnonzero(leaf_values == leaf)
    if not len(positions):
        return positions
    matched = frame.iloc[positions]
    first = ~matched[group_column].duplicated(keep="first")
    return positions[first.to_numpy()]


def _first_rule_rows(
    frame: pd.DataFrame,
    matrix: pd.DataFrame,
    rule: tuple[dict[str, Any], ...],
    group_column: str,
) -> np.ndarray:
    mask = np.ones(len(matrix), dtype=bool)
    for condition in rule:
        values = matrix[str(condition["feature"])].to_numpy(dtype=float)
        if condition["operator"] == ">":
            mask &= values > float(condition["value"])
        else:
            mask &= values <= float(condition["value"])
    positions = np.flatnonzero(mask)
    if not len(positions):
        return positions
    first = ~frame.iloc[positions][group_column].duplicated(keep="first")
    return positions[first.to_numpy()]


def _wilson_lower(successes: int, count: int) -> float:
    if count <= 0:
        return 0.0
    z = 1.959963984540054
    p = successes / count
    denominator = 1.0 + z * z / count
    center = p + z * z / (2.0 * count)
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * count)) / count)
    return (center - radius) / denominator


def _week_probabilities(
    frame: pd.DataFrame,
    labels: np.ndarray,
    group_column: str,
) -> np.ndarray:
    timestamps = pd.to_datetime(frame["__snapshot_ms"], unit="ms", utc=True)
    iso = timestamps.dt.isocalendar()
    weeks = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    table = pd.DataFrame(
        {
            "week": weeks.to_numpy(),
            "label": labels,
            "group": frame[group_column].astype(str).to_numpy(),
        }
    )
    sizes = table.groupby(["week", "group"], sort=False)["group"].transform("size")
    table["weight"] = 1.0 / sizes
    table["weighted_label"] = table["label"] * table["weight"]
    summary = table.groupby("week", sort=False).agg(
        weighted_label=("weighted_label", "sum"),
        weight=("weight", "sum"),
    )
    rates = summary["weighted_label"] / summary["weight"]
    return weeks.map(rates).to_numpy(dtype=float)


def _screen_search(
    prepared: PreparedPhenotypeData,
    config: CrossFittedPhenotypeConfig,
    *,
    labels_override: np.ndarray | None = None,
    collect_screening: bool = True,
) -> tuple[list[ScreenedLeaf], pd.DataFrame, tuple[tuple[str, np.ndarray, np.ndarray], ...]]:
    frame = prepared.search
    labels = (
        frame[config.input.label_column].to_numpy(dtype=np.int8)
        if labels_override is None else np.asarray(labels_override, dtype=np.int8)
    )
    folds = _cross_fit_slices(prepared, config)
    passed: list[ScreenedLeaf] = []
    rows: list[dict[str, object]] = []
    oof_probability_sum = np.zeros(len(frame), dtype=float)
    oof_probability_count = np.zeros(len(frame), dtype=np.int16)
    for fold_id, train, validation in folds:
        validation_frame = frame.iloc[validation].reset_index(drop=True)
        validation_labels = labels[validation]
        week_base = _week_probabilities(
            validation_frame, validation_labels, config.input.group_column
        )
        for generator in config.generators:
            model = _fit_model(
                prepared.x_search.iloc[train], labels[train],
                frame.iloc[train][config.input.group_column],
                config=config, seed=generator.seed, depth=generator.depth,
            )
            model_json = _export_model_json(model)
            oof_probability_sum[validation] += model.predict_proba(
                prepared.x_search.iloc[validation]
            )[:, 1]
            oof_probability_count[validation] += 1
            validation_leaves = np.asarray(
                model.calc_leaf_indexes(prepared.x_search.iloc[validation])
            )
            reference_leaves = np.asarray(model.calc_leaf_indexes(prepared.x_search))
            for tree_index, tree in enumerate(model_json["oblivious_trees"]):
                for leaf_index in np.unique(validation_leaves[:, tree_index]):
                    local = _first_rows_for_leaf(
                        validation_frame,
                        validation_leaves[:, tree_index],
                        int(leaf_index),
                        config.input.group_column,
                    )
                    count = len(local)
                    fades = int(validation_labels[local].sum()) if count else 0
                    rate = fades / count if count else 0.0
                    base = float(week_base[local].mean()) if count else 0.0
                    lift = rate / base if base > 0.0 else 0.0
                    lower = _wilson_lower(fades, count)
                    reason = "PASS"
                    if count < config.min_fold_events:
                        reason = "MIN_SUPPORT"
                    elif rate < config.min_fold_fade_rate:
                        reason = "MIN_RATE"
                    elif lift < config.min_fold_lift:
                        reason = "MIN_LIFT"
                    elif lower < config.min_fold_wilson_lower_95:
                        reason = "MIN_WILSON"
                    rule, rule_text, feature_names = _canonical_rule(
                        tree, int(leaf_index), model_json
                    )
                    passed_gate = reason == "PASS" and bool(rule)
                    if not rule:
                        reason = "EMPTY_RULE"
                    reference = _first_rows_for_leaf(
                        frame,
                        reference_leaves[:, tree_index],
                        int(leaf_index),
                        config.input.group_column,
                    )
                    candidate = ScreenedLeaf(
                        source_kind="direct_fold_leaf",
                        fold_id=fold_id,
                        generator_seed=generator.seed,
                        generator_depth=generator.depth,
                        tree_index=tree_index,
                        leaf_index=int(leaf_index),
                        rule=rule,
                        rule_text=rule_text,
                        feature_names=feature_names,
                        validation_count=count,
                        validation_fades=fades,
                        validation_rate=rate,
                        validation_base_rate=base,
                        validation_lift=lift,
                        validation_wilson_lower_95=lower,
                        passed=passed_gate,
                        rejection_reason=reason,
                        reference_groups=frozenset(
                            frame.iloc[reference][config.input.group_column].astype(str)
                        ),
                    )
                    if passed_gate:
                        passed.append(candidate)
                    if collect_screening:
                        rows.append(
                            {
                                **{
                                    key: value for key, value in asdict(candidate).items()
                                    if key not in {"rule", "reference_groups"}
                                },
                                "rule_json": json.dumps(rule, separators=(",", ":")),
                                "reference_group_count": len(candidate.reference_groups),
                            }
                        )
    oof_positions = np.flatnonzero(oof_probability_count > 0)
    if not len(oof_positions) or np.any(
        oof_probability_count[oof_positions] != len(config.generators)
    ):
        raise PhenotypeDiscoveryError("cross-fitted phenotype OOF coverage is incomplete")
    oof_probability = np.full(len(frame), np.nan)
    oof_probability[oof_positions] = (
        oof_probability_sum[oof_positions] / oof_probability_count[oof_positions]
    )
    surrogate_passed, surrogate_rows = _screen_oof_risk_surrogates(
        prepared,
        config,
        folds=folds,
        labels=labels,
        oof_positions=oof_positions,
        oof_probability=oof_probability,
        collect_screening=collect_screening,
    )
    passed.extend(surrogate_passed)
    if collect_screening:
        rows.extend(surrogate_rows)
    return passed, pd.DataFrame(rows), folds


def _screen_oof_risk_surrogates(
    prepared: PreparedPhenotypeData,
    config: CrossFittedPhenotypeConfig,
    *,
    folds: tuple[tuple[str, np.ndarray, np.ndarray], ...],
    labels: np.ndarray,
    oof_positions: np.ndarray,
    oof_probability: np.ndarray,
    collect_screening: bool,
) -> tuple[list[ScreenedLeaf], list[dict[str, object]]]:
    frame = prepared.search
    passed: list[ScreenedLeaf] = []
    rows: list[dict[str, object]] = []
    fold_base = {
        fold_id: _week_probabilities(
            frame.iloc[validation].reset_index(drop=True),
            labels[validation],
            config.input.group_column,
        )
        for fold_id, _, validation in folds
    }
    for generator in config.generators:
        model = _fit_risk_surrogate(
            prepared.x_search.iloc[oof_positions],
            oof_probability[oof_positions],
            config=config,
            seed=generator.seed + 1_000_003,
            depth=generator.depth,
        )
        model_json = _export_model_json(model)
        leaves = np.asarray(model.calc_leaf_indexes(prepared.x_search))
        for tree_index, tree in enumerate(model_json["oblivious_trees"]):
            for leaf_index in np.unique(leaves[oof_positions, tree_index]):
                reference = _first_rows_for_leaf(
                    frame,
                    leaves[:, tree_index],
                    int(leaf_index),
                    config.input.group_column,
                )
                rule, rule_text, feature_names = _canonical_rule(
                    tree, int(leaf_index), model_json
                )
                reference_groups = frozenset(
                    frame.iloc[reference][config.input.group_column].astype(str)
                )
                for fold_id, _, validation in folds:
                    validation_frame = frame.iloc[validation].reset_index(drop=True)
                    local = _first_rows_for_leaf(
                        validation_frame,
                        leaves[validation, tree_index],
                        int(leaf_index),
                        config.input.group_column,
                    )
                    count = len(local)
                    fold_labels = labels[validation]
                    fades = int(fold_labels[local].sum()) if count else 0
                    rate = fades / count if count else 0.0
                    base = float(fold_base[fold_id][local].mean()) if count else 0.0
                    lift = rate / base if base > 0.0 else 0.0
                    lower = _wilson_lower(fades, count)
                    reason = "PASS"
                    if count < config.min_fold_events:
                        reason = "MIN_SUPPORT"
                    elif rate < config.min_fold_fade_rate:
                        reason = "MIN_RATE"
                    elif lift < config.min_fold_lift:
                        reason = "MIN_LIFT"
                    elif lower < config.min_fold_wilson_lower_95:
                        reason = "MIN_WILSON"
                    candidate = ScreenedLeaf(
                        source_kind="oof_risk_surrogate_leaf",
                        fold_id=fold_id,
                        generator_seed=generator.seed,
                        generator_depth=generator.depth,
                        tree_index=tree_index,
                        leaf_index=int(leaf_index),
                        rule=rule,
                        rule_text=rule_text,
                        feature_names=feature_names,
                        validation_count=count,
                        validation_fades=fades,
                        validation_rate=rate,
                        validation_base_rate=base,
                        validation_lift=lift,
                        validation_wilson_lower_95=lower,
                        passed=reason == "PASS" and bool(rule),
                        rejection_reason=reason if rule else "EMPTY_RULE",
                        reference_groups=reference_groups,
                    )
                    if candidate.passed:
                        passed.append(candidate)
                    if collect_screening:
                        rows.append(
                            {
                                **{
                                    key: value for key, value in asdict(candidate).items()
                                    if key not in {"rule", "reference_groups"}
                                },
                                "rule_json": json.dumps(rule, separators=(",", ":")),
                                "reference_group_count": len(reference_groups),
                            }
                        )
    return passed, rows


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = len(left | right)
    return len(left & right) / union if union else 1.0


def _freeze_candidates(
    candidates: list[ScreenedLeaf],
    prepared: PreparedPhenotypeData,
    folds: tuple[tuple[str, np.ndarray, np.ndarray], ...],
    config: CrossFittedPhenotypeConfig,
    *,
    labels_override: np.ndarray | None = None,
) -> list[FrozenPhenotype]:
    ranked = sorted(
        candidates,
        key=lambda row: (row.validation_wilson_lower_95, row.validation_rate, row.validation_count),
        reverse=True,
    )
    clusters: list[list[ScreenedLeaf]] = []
    for candidate in ranked:
        for cluster in clusters:
            if _jaccard(candidate.reference_groups, cluster[0].reference_groups) >= config.consensus_membership_jaccard:
                cluster.append(candidate)
                break
        else:
            clusters.append([candidate])
    eligible: list[tuple[ScreenedLeaf, list[ScreenedLeaf]]] = []
    for cluster in clusters:
        folds_seen = {row.fold_id for row in cluster}
        generators = {(row.generator_seed, row.generator_depth) for row in cluster}
        if len(folds_seen) < config.min_fold_support_count or len(generators) < config.min_generator_support_count:
            continue
        representative = max(
            cluster,
            key=lambda row: (
                sum(_jaccard(row.reference_groups, other.reference_groups) for other in cluster),
                row.validation_wilson_lower_95,
            ),
        )
        eligible.append((representative, cluster))
    eligible.sort(
        key=lambda pair: (
            len({row.fold_id for row in pair[1]}),
            len({(row.generator_seed, row.generator_depth) for row in pair[1]}),
            pair[0].validation_wilson_lower_95,
        ),
        reverse=True,
    )
    frozen: list[FrozenPhenotype] = []
    for representative, cluster in eligible:
        if any(
            _jaccard(representative.reference_groups, row.representative.reference_groups)
            > config.max_frozen_membership_jaccard
            for row in frozen
        ):
            continue
        oof_positions: list[np.ndarray] = []
        for _, _, validation in folds:
            local = _first_rule_rows(
                prepared.search.iloc[validation].reset_index(drop=True),
                prepared.x_search.iloc[validation].reset_index(drop=True),
                representative.rule,
                config.input.group_column,
            )
            oof_positions.append(validation[local])
        positions = np.unique(np.concatenate(oof_positions)) if oof_positions else np.array([], dtype=int)
        labels = (
            prepared.search.iloc[positions][config.input.label_column].to_numpy(np.int8)
            if labels_override is None
            else np.asarray(labels_override, dtype=np.int8)[positions]
        )
        count = len(labels)
        fades = int(labels.sum())
        frozen.append(
            FrozenPhenotype(
                phenotype_id=f"fade_phenotype_{len(frozen) + 1:03d}",
                representative=representative,
                fold_support_count=len({row.fold_id for row in cluster}),
                generator_support_count=len({(row.generator_seed, row.generator_depth) for row in cluster}),
                cluster_rule_count=len(cluster),
                search_oof_count=count,
                search_oof_fades=fades,
                search_oof_rate=fades / count if count else 0.0,
                search_oof_wilson_lower_95=_wilson_lower(fades, count),
            )
        )
        if len(frozen) >= config.max_frozen_phenotypes:
            break
    return frozen


def _evaluate_frozen(
    frozen: list[FrozenPhenotype],
    prepared: PreparedPhenotypeData,
    config: CrossFittedPhenotypeConfig,
    *,
    controls_passed: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    calibration_labels = prepared.calibration[config.input.label_column].to_numpy(np.int8)
    verification_labels = prepared.verification[config.input.label_column].to_numpy(np.int8)
    calibration_base = float(
        prepared.calibration.drop_duplicates(config.input.group_column)[config.input.label_column].mean()
    )
    verification_week_base = _week_probabilities(
        prepared.verification, verification_labels, config.input.group_column
    )
    verification_positions: dict[str, np.ndarray] = {}
    p_values: list[float] = []
    for phenotype in frozen:
        rule = phenotype.representative.rule
        calibration = _first_rule_rows(
            prepared.calibration, prepared.x_calibration, rule, config.input.group_column
        )
        cal_labels = calibration_labels[calibration]
        phenotype.calibration_count = len(calibration)
        phenotype.calibration_fades = int(cal_labels.sum())
        phenotype.calibration_rate = float(cal_labels.mean()) if len(cal_labels) else 0.0
        phenotype.calibration_wilson_lower_95 = _wilson_lower(
            phenotype.calibration_fades, phenotype.calibration_count
        )
        phenotype.calibrated_probability = (
            phenotype.calibration_fades + config.beta_prior_strength * calibration_base
        ) / (phenotype.calibration_count + config.beta_prior_strength)
        verification = _first_rule_rows(
            prepared.verification, prepared.x_verification, rule, config.input.group_column
        )
        verification_positions[phenotype.phenotype_id] = verification
        ver_labels = verification_labels[verification]
        phenotype.verification_count = len(verification)
        phenotype.verification_fades = int(ver_labels.sum())
        phenotype.verification_rate = float(ver_labels.mean()) if len(ver_labels) else 0.0
        phenotype.verification_wilson_lower_95 = _wilson_lower(
            phenotype.verification_fades, phenotype.verification_count
        )
        phenotype.verification_calibration_gap = abs(
            phenotype.verification_rate - phenotype.calibrated_probability
        ) if len(ver_labels) else 1.0
        p_value, positive_fraction = _week_sign_flip_test(
            prepared.verification,
            verification,
            verification_labels,
            verification_week_base,
            seed=config.random_seed + len(p_values) * 101,
        )
        phenotype.verification_p_value = p_value
        phenotype.verification_positive_week_fraction = positive_fraction
        p_values.append(p_value)
    q_values = _benjamini_yekutieli(p_values)
    for phenotype, q_value in zip(frozen, q_values, strict=True):
        phenotype.verification_q_value = q_value
        calibration_candidate = (
            phenotype.calibration_count >= config.calibration_min_events
            and phenotype.calibrated_probability >= config.calibration_candidate_probability
        )
        verified = (
            calibration_candidate
            and phenotype.verification_count >= config.verification_min_events
            and phenotype.verification_wilson_lower_95 > 0.50
            and phenotype.verification_q_value <= config.fdr_alpha
            and phenotype.verification_positive_week_fraction
            >= config.verification_min_positive_week_fraction
        )
        high = (
            verified
            and phenotype.calibrated_probability >= config.high_probability_threshold
            and phenotype.verification_rate >= config.verification_min_observed_rate
            and phenotype.verification_wilson_lower_95
            >= config.verification_min_wilson_lower_95
            and phenotype.verification_calibration_gap
            <= config.verification_max_calibration_gap
        )
        if not controls_passed:
            phenotype.status = "CONTROL_FAILED"
        elif high:
            phenotype.status = "HIGH_PROBABILITY_VERIFIED"
        elif verified:
            phenotype.status = "VERIFIED_PHENOTYPE"
        elif calibration_candidate:
            phenotype.status = "VERIFICATION_REJECTED"
        else:
            phenotype.status = "CALIBRATION_REJECTED"
    catalog = _catalog_frame(frozen)
    assignments = _assignment_frame(
        frozen, verification_positions, prepared.verification, config
    )
    coverage = _coverage_frame(frozen, verification_positions, prepared, config)
    return catalog, assignments, coverage


def _week_sign_flip_test(
    frame: pd.DataFrame,
    positions: np.ndarray,
    labels: np.ndarray,
    base: np.ndarray,
    *,
    seed: int,
) -> tuple[float, float]:
    if not len(positions):
        return 1.0, 0.0
    timestamps = pd.to_datetime(frame.iloc[positions]["__snapshot_ms"], unit="ms", utc=True)
    iso = timestamps.dt.isocalendar()
    weeks = (iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)).to_numpy()
    residual = labels[positions].astype(float) - base[positions]
    unique = pd.unique(weeks)
    sums = np.asarray([residual[weeks == week].sum() for week in unique])
    observed = float(residual.mean())
    rng = np.random.default_rng(seed)
    null = np.empty(999, dtype=float)
    for index in range(len(null)):
        signs = rng.choice(np.asarray([-1.0, 1.0]), size=len(sums))
        null[index] = float(np.sum(signs * sums) / len(residual))
    p = float((1 + np.count_nonzero(null >= observed)) / (len(null) + 1))
    positive = [float(residual[weeks == week].mean()) > 0.0 for week in unique]
    return p, float(np.mean(positive)) if positive else 0.0


def _benjamini_yekutieli(p_values: list[float]) -> list[float]:
    count = len(p_values)
    if not count:
        return []
    harmonic = sum(1.0 / index for index in range(1, count + 1))
    order = np.argsort(p_values)
    adjusted = np.ones(count)
    running = 1.0
    for rank_index in range(count - 1, -1, -1):
        original = int(order[rank_index])
        rank = rank_index + 1
        value = min(1.0, p_values[original] * count * harmonic / rank)
        running = min(running, value)
        adjusted[original] = running
    return adjusted.tolist()


def _catalog_frame(frozen: list[FrozenPhenotype]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for phenotype in frozen:
        rows.append(
            {
                "phenotype_id": phenotype.phenotype_id,
                "status": phenotype.status,
                "rule_text": phenotype.representative.rule_text,
                "rule_json": json.dumps(phenotype.representative.rule, separators=(",", ":")),
                "feature_names": ";".join(phenotype.representative.feature_names),
                "fold_support_count": phenotype.fold_support_count,
                "generator_support_count": phenotype.generator_support_count,
                "cluster_rule_count": phenotype.cluster_rule_count,
                "search_oof_count": phenotype.search_oof_count,
                "search_oof_fades": phenotype.search_oof_fades,
                "search_oof_rate": phenotype.search_oof_rate,
                "search_oof_wilson_lower_95": phenotype.search_oof_wilson_lower_95,
                "calibration_count": phenotype.calibration_count,
                "calibration_fades": phenotype.calibration_fades,
                "calibration_rate": phenotype.calibration_rate,
                "calibrated_probability": phenotype.calibrated_probability,
                "calibration_wilson_lower_95": phenotype.calibration_wilson_lower_95,
                "verification_count": phenotype.verification_count,
                "verification_fades": phenotype.verification_fades,
                "verification_rate": phenotype.verification_rate,
                "verification_wilson_lower_95": phenotype.verification_wilson_lower_95,
                "verification_calibration_gap": phenotype.verification_calibration_gap,
                "verification_p_value": phenotype.verification_p_value,
                "verification_q_value": phenotype.verification_q_value,
                "verification_positive_week_fraction": phenotype.verification_positive_week_fraction,
            }
        )
    return pd.DataFrame(rows)


def _assignment_frame(
    frozen: list[FrozenPhenotype],
    positions: dict[str, np.ndarray],
    verification: pd.DataFrame,
    config: CrossFittedPhenotypeConfig,
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    columns = [
        config.input.group_column, config.input.split_group_column,
        config.input.symbol_column, "__snapshot_ms", config.input.label_column,
    ]
    for phenotype in frozen:
        selected = verification.iloc[positions[phenotype.phenotype_id]][columns].copy()
        selected.insert(0, "phenotype_id", phenotype.phenotype_id)
        selected.insert(1, "status", phenotype.status)
        selected.insert(2, "frozen_probability", phenotype.calibrated_probability)
        rows.append(selected)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["phenotype_id", "status", "frozen_probability", *columns]
    )


def _coverage_frame(
    frozen: list[FrozenPhenotype],
    positions: dict[str, np.ndarray],
    prepared: PreparedPhenotypeData,
    config: CrossFittedPhenotypeConfig,
) -> pd.DataFrame:
    total_groups = prepared.verification[config.input.group_column].nunique()
    rows: list[dict[str, object]] = []
    for label, predicate in (
        ("all_frozen", lambda row: True),
        ("verified", lambda row: row.status in {"VERIFIED_PHENOTYPE", "HIGH_PROBABILITY_VERIFIED"}),
        ("high_probability_verified", lambda row: row.status == "HIGH_PROBABILITY_VERIFIED"),
    ):
        covered: set[str] = set()
        for phenotype in frozen:
            if predicate(phenotype):
                covered.update(
                    prepared.verification.iloc[positions[phenotype.phenotype_id]][
                        config.input.group_column
                    ].astype(str)
                )
        rows.append(
            {
                "coverage_kind": label,
                "verification_group_count": total_groups,
                "covered_group_count": len(covered),
                "coverage_fraction": len(covered) / total_groups if total_groups else 0.0,
                "unclassified_group_count": total_groups - len(covered),
            }
        )
    return pd.DataFrame(rows)


def _calendar_block_permute(
    frame: pd.DataFrame,
    labels: np.ndarray,
    seed: int,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    timestamps = pd.to_datetime(frame["__snapshot_ms"], unit="ms", utc=True)
    blocks = timestamps.dt.strftime("%Y-%m").to_numpy()
    result = labels.copy()
    for block in pd.unique(blocks):
        positions = np.flatnonzero(blocks == block)
        result[positions] = result[rng.permutation(positions)]
    return result


def build_cross_fitted_phenotypes(
    frame: pd.DataFrame,
    config: CrossFittedPhenotypeConfig,
    *,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> PhenotypeDiscoveryResult:
    prepared = prepare_phenotype_data(frame, config)
    candidates, screening, folds = _screen_search(prepared, config)
    frozen = _freeze_candidates(candidates, prepared, folds, config)
    if progress_callback is not None:
        progress_callback("real_search", 1, 1)
    labels = prepared.search[config.input.label_column].to_numpy(np.int8)
    def run_null(index: int) -> dict[str, object]:
        null_config = replace(
            config,
            catboost_thread_count=max(
                1, math.ceil(config.catboost_thread_count / config.null_workers)
            ),
        )
        shuffled = _calendar_block_permute(
            prepared.search, labels, config.random_seed + 10_000 + index * 131
        )
        null_candidates, _, null_folds = _screen_search(
            prepared, null_config, labels_override=shuffled, collect_screening=False
        )
        null_frozen = _freeze_candidates(
            null_candidates,
            prepared,
            null_folds,
            null_config,
            labels_override=shuffled,
        )
        return {
            "control": "calendar_block_label_permutation",
            "iteration": index,
            "frozen_phenotype_count": len(null_frozen),
            "best_search_oof_rate": max(
                (row.search_oof_rate for row in null_frozen), default=0.0
            ),
        }

    null_rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=config.null_workers) as executor:
        futures = {
            executor.submit(run_null, index): index
            for index in range(config.null_permutations)
        }
        for future in as_completed(futures):
            null_rows.append(future.result())
            if progress_callback is not None:
                progress_callback("null_controls", len(null_rows), config.null_permutations)
    controls = pd.DataFrame(null_rows).sort_values("iteration", kind="mergesort")
    count_p = float(
        (1 + np.count_nonzero(controls["frozen_phenotype_count"] >= len(frozen)))
        / (len(controls) + 1)
    )
    best_real = max((row.search_oof_rate for row in frozen), default=0.0)
    best_p = float(
        (1 + np.count_nonzero(controls["best_search_oof_rate"] >= best_real))
        / (len(controls) + 1)
    )
    controls["observed_frozen_phenotype_count"] = len(frozen)
    controls["observed_best_search_oof_rate"] = best_real
    controls["count_empirical_p_value"] = count_p
    controls["best_rate_empirical_p_value"] = best_p
    controls_passed = count_p <= 0.05 and best_p <= 0.05
    catalog, assignments, coverage = _evaluate_frozen(
        frozen, prepared, config, controls_passed=controls_passed
    )
    if progress_callback is not None:
        progress_callback("calibration_verification", 1, 1)
    rules = tuple(
        {
            "phenotype_id": row.phenotype_id,
            "rule": row.representative.rule,
            "rule_text": row.representative.rule_text,
            "calibrated_probability": row.calibrated_probability,
            "status": row.status,
        }
        for row in frozen
    )
    feature_transform = {
        "numeric_imputation": prepared.imputation_values,
        "numeric_missing_indicators": list(config.numeric_features),
        "categorical_levels": prepared.categorical_levels,
        "unknown_category_policy": "explicit_unknown_indicator_v1",
        "fit_scope": "search_period_features_only",
    }
    verified = int(catalog["status"].isin(["VERIFIED_PHENOTYPE", "HIGH_PROBABILITY_VERIFIED"]).sum()) if not catalog.empty else 0
    high = int((catalog["status"] == "HIGH_PROBABILITY_VERIFIED").sum()) if not catalog.empty else 0
    followup_registry = build_phenotype_followup_registry(catalog, config)
    return PhenotypeDiscoveryResult(
        catalog=catalog,
        followup_registry=followup_registry,
        screening=screening,
        assignments=assignments,
        coverage=coverage,
        controls=controls,
        frozen_rules=rules,
        feature_transform=feature_transform,
        search_row_count=len(prepared.search),
        calibration_row_count=len(prepared.calibration),
        verification_row_count=len(prepared.verification),
        candidate_count=len(candidates),
        frozen_count=len(frozen),
        verified_count=verified,
        high_probability_verified_count=high,
    )


__all__ = [
    "PhenotypeDiscoveryError",
    "build_cross_fitted_phenotypes",
    "prepare_phenotype_data",
]
