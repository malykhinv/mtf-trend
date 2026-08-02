from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Iterable

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import log_loss, roc_auc_score

from anomaly_science.probability.config import (
    DIRECT_ISOTONIC_V1,
    BinaryWeeklyWalkForwardConfig,
)


class BinaryProbabilityInputError(ValueError):
    """Raised when an input frame violates the causal binary-target contract."""


@dataclass(slots=True)
class FrozenWeeklyBinaryModel:
    test_week: str
    model_id: str
    model: CatBoostClassifier
    calibrator: IsotonicRegression
    feature_names: tuple[str, ...]
    categorical_feature_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BinaryWalkForwardResult:
    predictions: pd.DataFrame
    weekly_metadata: pd.DataFrame
    feature_importance: pd.DataFrame
    frozen_models: tuple[FrozenWeeklyBinaryModel, ...]


@dataclass(frozen=True, slots=True)
class _WeeklyBuildPart:
    prediction: pd.DataFrame | None
    metadata: dict[str, object]
    importance: tuple[dict[str, object], ...]
    frozen_model: FrozenWeeklyBinaryModel | None


def build_binary_weekly_walk_forward(
    frame: pd.DataFrame,
    config: BinaryWeeklyWalkForwardConfig,
    *,
    weekly_jobs: int = 1,
) -> BinaryWalkForwardResult:
    if not 1 <= weekly_jobs <= 8:
        raise ValueError("weekly_jobs must be between 1 and 8")
    work = validate_binary_probability_frame(frame, config)
    oos = work.loc[
        (work[config.snapshot_time_column] >= config.oos_start_ms)
        & (work[config.snapshot_time_column] < config.oos_end_ms)
    ].copy()
    oos["test_week"] = oos[config.snapshot_time_column].map(_utc_week)
    prediction_parts: list[pd.DataFrame] = []
    metadata: list[dict[str, object]] = []
    importance: list[dict[str, object]] = []
    frozen_models: list[FrozenWeeklyBinaryModel] = []

    weeks = tuple(
        (str(test_week), test.copy())
        for test_week, test in oos.groupby("test_week", sort=True)
    )
    if weekly_jobs == 1:
        parts = tuple(
            _build_binary_week(work, test_week, test, config)
            for test_week, test in weeks
        )
    else:
        with ThreadPoolExecutor(max_workers=min(weekly_jobs, len(weeks))) as executor:
            futures = tuple(
                executor.submit(_build_binary_week, work, test_week, test, config)
                for test_week, test in weeks
            )
            parts = tuple(future.result() for future in futures)
    for part in parts:
        if part.prediction is not None:
            prediction_parts.append(part.prediction)
        metadata.append(part.metadata)
        importance.extend(part.importance)
        if part.frozen_model is not None:
            frozen_models.append(part.frozen_model)

    predictions = (
        pd.concat(prediction_parts, ignore_index=True)
        if prediction_parts
        else pd.DataFrame(columns=_prediction_columns(config))
    )
    return BinaryWalkForwardResult(
        predictions=predictions,
        weekly_metadata=pd.DataFrame(metadata),
        feature_importance=pd.DataFrame(importance),
        frozen_models=tuple(frozen_models),
    )


def _build_binary_week(
    work: pd.DataFrame,
    test_week: str,
    test: pd.DataFrame,
    config: BinaryWeeklyWalkForwardConfig,
) -> _WeeklyBuildPart:
    split_group_column = config.split_group_column or config.group_column
    weight_group_column = config.weight_group_column or config.group_column
    freeze_ms = _week_start_ms(test_week)
    test = test.sort_values(
        [config.snapshot_time_column, config.symbol_column, config.group_column],
        kind="mergesort",
    ).copy()
    test_groups = set(test[split_group_column].astype(str))
    train_before_split_exclusion = work.loc[
        (work[config.snapshot_time_column] >= config.development_start_ms)
        & (work[config.resolution_time_column] < freeze_ms)
    ].copy()
    excluded_same_split_group = train_before_split_exclusion[
        split_group_column
    ].astype(str).isin(test_groups)
    train = train_before_split_exclusion.loc[~excluded_same_split_group].copy()
    train = train.sort_values(
        [config.snapshot_time_column, config.symbol_column, config.group_column],
        kind="mergesort",
    )
    base_metadata = {
        "test_week": test_week,
        "weekly_model_freeze_time_ms": freeze_ms,
        "oos_row_count": len(test),
        "eligible_train_row_count": len(train),
        "eligible_train_group_count": train[config.group_column].nunique(),
        "eligible_train_weight_group_count": train[weight_group_column].nunique(),
        "eligible_train_split_group_count": train[split_group_column].nunique(),
        "test_split_group_count": len(test_groups),
        "excluded_train_same_split_group_row_count": int(excluded_same_split_group.sum()),
        "latest_train_resolution_time_ms": (
            int(train[config.resolution_time_column].max()) if not train.empty else pd.NA
        ),
    }

    def skipped(reason: str) -> _WeeklyBuildPart:
        return _WeeklyBuildPart(
            prediction=None,
            metadata={**base_metadata, "status": "SKIPPED", "reason": reason},
            importance=(),
            frozen_model=None,
        )

    if len(train) < config.min_train_rows:
        return skipped("min_train_rows")
    split = _chronological_group_split(train, config)
    if isinstance(split, str):
        return skipped(split)
    fit, validation, calibration = split
    invalid_reason = _split_support_reason(fit, validation, calibration, config)
    if invalid_reason:
        return skipped(invalid_reason)
    active_numeric, active_categorical, dropped = _active_features(fit, config)
    active_features = (*active_numeric, *active_categorical)
    if not active_features:
        return skipped("no_active_features")
    x_fit = _model_matrix(fit, active_numeric, active_categorical)
    x_validation = _model_matrix(validation, active_numeric, active_categorical)
    x_calibration = _model_matrix(calibration, active_numeric, active_categorical)
    x_test = _model_matrix(test, active_numeric, active_categorical)
    y_fit = fit[config.label_column].to_numpy(dtype=np.int8)
    y_validation = validation[config.label_column].to_numpy(dtype=np.int8)
    y_calibration = calibration[config.label_column].to_numpy(dtype=np.int8)
    cat_indices = [x_fit.columns.get_loc(name) for name in active_categorical]
    model = CatBoostClassifier(
        loss_function="Logloss",
        eval_metric="Logloss",
        iterations=config.catboost_iterations,
        depth=config.catboost_depth,
        learning_rate=config.catboost_learning_rate,
        l2_leaf_reg=config.catboost_l2_leaf_reg,
        random_seed=config.random_seed,
        thread_count=config.catboost_thread_count,
        allow_writing_files=False,
        verbose=False,
    )
    model.fit(
        x_fit,
        y_fit,
        cat_features=cat_indices,
        sample_weight=_event_normalized_weights(fit, weight_group_column),
        eval_set=(x_validation, y_validation),
        early_stopping_rounds=config.catboost_early_stopping_rounds,
        use_best_model=True,
        verbose=False,
    )
    baseline = float(
        np.average(
            train[config.label_column].to_numpy(dtype=float),
            weights=_event_normalized_weights(train, weight_group_column),
        )
    )
    raw_calibration = model.predict_proba(x_calibration)[:, 1]
    raw_validation = model.predict_proba(x_validation)[:, 1]
    calibrator = _fit_isotonic_calibrator(
        raw_calibration,
        y_calibration,
        weights=_event_normalized_weights(calibration, weight_group_column),
        baseline=baseline,
        config=config,
    )
    raw_test = model.predict_proba(x_test)[:, 1]
    calibrated_test = np.asarray(calibrator.predict(raw_test), dtype=float)
    model_id = f"{config.protocol_freeze_id}:{test_week}"
    prediction = _prediction_frame(
        test,
        config=config,
        test_week=test_week,
        freeze_ms=freeze_ms,
        model_id=model_id,
        raw=raw_test,
        calibrated=calibrated_test,
        baseline=baseline,
    )
    metadata = {
        **base_metadata,
        "status": "FROZEN_AND_SCORED",
        "reason": "",
        "model_id": model_id,
        "fit_row_count": len(fit),
        "validation_row_count": len(validation),
        "calibration_row_count": len(calibration),
        "fit_group_count": fit[config.group_column].nunique(),
        "validation_group_count": validation[config.group_column].nunique(),
        "calibration_group_count": calibration[config.group_column].nunique(),
        "fit_positive_rate": float(fit[config.label_column].mean()),
        "validation_positive_rate": float(validation[config.label_column].mean()),
        "calibration_positive_rate": float(calibration[config.label_column].mean()),
        "weekly_frozen_baseline_probability": baseline,
        "best_iteration": int(model.get_best_iteration()),
        "validation_raw_log_loss": float(
            log_loss(y_validation, raw_validation, labels=[0, 1])
        ),
        "calibration_raw_log_loss": float(
            log_loss(y_calibration, raw_calibration, labels=[0, 1])
        ),
        "calibration_isotonic_log_loss": float(
            log_loss(
                y_calibration,
                np.asarray(calibrator.predict(raw_calibration), dtype=float),
                labels=[0, 1],
            )
        ),
        "active_features": ";".join(active_features),
        "dropped_constant_or_all_missing_features": ";".join(dropped),
        "calibrator_knot_count": len(calibrator.X_thresholds_),
        "calibration_method": config.calibration_method,
    }
    importance = tuple(
        {
            "test_week": test_week,
            "model_id": model_id,
            "feature_name": name,
            "importance": float(value),
        }
        for name, value in zip(active_features, model.get_feature_importance())
    )
    frozen_model = FrozenWeeklyBinaryModel(
        test_week=test_week,
        model_id=model_id,
        model=model,
        calibrator=calibrator,
        feature_names=tuple(active_features),
        categorical_feature_names=tuple(active_categorical),
    )
    return _WeeklyBuildPart(prediction, metadata, importance, frozen_model)


def validate_binary_probability_frame(
    frame: pd.DataFrame,
    config: BinaryWeeklyWalkForwardConfig,
) -> pd.DataFrame:
    required = {
        config.group_column,
        config.symbol_column,
        config.snapshot_time_column,
        config.feature_cutoff_time_column,
        config.future_start_time_column,
        config.resolution_time_column,
        config.label_column,
        config.label_available_column,
        config.row_filter_column,
        config.required_label_schema_column,
        *config.numeric_features,
        *config.categorical_features,
        *config.breakdown_columns,
        *config.required_true_columns,
        *config.required_finite_columns,
    }
    if config.split_group_column:
        required.add(config.split_group_column)
    if config.weight_group_column:
        required.add(config.weight_group_column)
    if config.population_minimum_column:
        required.add(config.population_minimum_column)
    if config.population_exact_column:
        required.add(config.population_exact_column)
    if config.required_input_schema_column:
        required.add(config.required_input_schema_column)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise BinaryProbabilityInputError(f"binary probability input columns are missing: {missing}")
    work = frame.loc[
        _bool_series(frame[config.row_filter_column], name=config.row_filter_column)
        == config.row_filter_value
    ].copy()
    for name in config.required_true_columns:
        work = work.loc[_bool_series(work[name], name=name)].copy()
    for name in config.required_finite_columns:
        numeric = pd.to_numeric(work[name], errors="raise")
        work = work.loc[numeric.notna() & np.isfinite(numeric.to_numpy(dtype=float))].copy()
    if config.population_minimum_column:
        population_value = pd.to_numeric(
            work[config.population_minimum_column], errors="raise"
        )
        work = work.loc[population_value >= float(config.population_minimum_value)].copy()
    if config.population_exact_column:
        work = work.loc[
            work[config.population_exact_column] == config.population_exact_value
        ].copy()
    work = work.loc[
        _bool_series(work[config.label_available_column], name=config.label_available_column)
    ].copy()
    if work.empty:
        raise BinaryProbabilityInputError("no labeled rows remain after the registered population filter")
    schema_values = set(work[config.required_label_schema_column].dropna().astype(str).unique())
    if schema_values != {config.required_label_schema_value}:
        raise BinaryProbabilityInputError(
            f"label schema mismatch: observed={sorted(schema_values)}, "
            f"required={config.required_label_schema_value!r}"
        )
    if config.required_input_schema_column:
        input_schema_values = set(
            work[config.required_input_schema_column].dropna().astype(str).unique()
        )
        if input_schema_values != {config.required_input_schema_value}:
            raise BinaryProbabilityInputError(
                f"input schema mismatch: observed={sorted(input_schema_values)}, "
                f"required={config.required_input_schema_value!r}"
            )
    for name in (
        config.snapshot_time_column,
        config.feature_cutoff_time_column,
        config.future_start_time_column,
        config.resolution_time_column,
        config.label_column,
    ):
        work[name] = pd.to_numeric(work[name], errors="raise")
        if work[name].isna().any():
            raise BinaryProbabilityInputError(f"{name} contains missing values in labeled rows")
    labels = set(work[config.label_column].astype(int).unique())
    if not labels <= {0, 1} or len(labels) < 2:
        raise BinaryProbabilityInputError(f"binary label must contain both 0 and 1; observed={sorted(labels)}")
    work[config.label_column] = work[config.label_column].astype(np.int8)
    if (work[config.feature_cutoff_time_column] > work[config.snapshot_time_column]).any():
        raise BinaryProbabilityInputError("feature_cutoff_time exceeds snapshot_time")
    if (work[config.future_start_time_column] <= work[config.snapshot_time_column]).any():
        raise BinaryProbabilityInputError("future_start_time must be strictly after snapshot_time")
    if (work[config.resolution_time_column] < work[config.future_start_time_column]).any():
        raise BinaryProbabilityInputError("resolution_time precedes future_start_time")
    if work[[config.group_column, config.snapshot_time_column]].duplicated().any():
        raise BinaryProbabilityInputError("group/snapshot rows must be unique")
    for name in config.numeric_features:
        work[name] = pd.to_numeric(work[name], errors="raise")
        if np.isinf(work[name].to_numpy(dtype=float)).any():
            raise BinaryProbabilityInputError(f"numeric feature {name!r} contains infinity")
    for name in config.categorical_features:
        work[name] = work[name].astype("string").fillna("__MISSING__")
    return work.sort_values(
        [config.snapshot_time_column, config.symbol_column, config.group_column],
        kind="mergesort",
    ).reset_index(drop=True)


def build_prediction_metrics(
    predictions: pd.DataFrame,
    config: BinaryWeeklyWalkForwardConfig,
) -> pd.DataFrame:
    if predictions.empty:
        return pd.DataFrame(columns=("slice", "slice_value", "metric", "value", "row_count"))
    rows: list[dict[str, object]] = []
    slices: list[tuple[str, str, pd.DataFrame]] = [("overall", "all", predictions)]
    slices.extend(("week", str(key), part) for key, part in predictions.groupby("test_week", sort=True))
    month = pd.to_datetime(predictions["snapshot_time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    for key, indices in month.groupby(month).groups.items():
        slices.append(("month", str(key), predictions.loc[indices]))
    slices.extend(
        ("symbol", str(key), part)
        for key, part in predictions.groupby("symbol", sort=True)
    )
    for column in config.breakdown_columns:
        slices.extend(
            (column, str(key), part)
            for key, part in predictions.groupby(column, sort=True, dropna=False)
        )
    for slice_name, slice_value, part in slices:
        y = part["target"].to_numpy(dtype=int)
        p = part["calibrated_probability"].to_numpy(dtype=float)
        baseline = part["weekly_frozen_baseline_probability"].to_numpy(dtype=float)
        metrics = _metric_values(y, p, baseline, config.calibration_bins)
        rows.extend(
            {
                "slice": slice_name,
                "slice_value": slice_value,
                "metric": name,
                "value": value,
                "row_count": len(part),
            }
            for name, value in metrics.items()
        )
    return pd.DataFrame(rows)


def build_reliability_rows(
    predictions: pd.DataFrame,
    config: BinaryWeeklyWalkForwardConfig,
) -> pd.DataFrame:
    columns = (
        "kind", "lower_bound", "upper_bound", "threshold", "row_count",
        "mean_probability", "observed_rate", "wilson_lower_95", "absolute_gap",
    )
    if predictions.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    p = predictions["calibrated_probability"].to_numpy(dtype=float)
    y = predictions["target"].to_numpy(dtype=int)
    edges = np.linspace(0.0, 1.0, config.calibration_bins + 1)
    bin_index = np.minimum(np.searchsorted(edges, p, side="right") - 1, config.calibration_bins - 1)
    for index in range(config.calibration_bins):
        mask = bin_index == index
        rows.append(_reliability_row("fixed_bin", mask, p, y, edges[index], edges[index + 1], math.nan))
    thresholds = tuple(
        sorted(
            {
                *config.reliability_thresholds,
                config.gates.high_probability_threshold,
            }
        )
    )
    for threshold in thresholds:
        rows.append(_reliability_row("threshold", p >= threshold, p, y, threshold, 1.0, threshold))
    return pd.DataFrame(rows, columns=columns)


def build_null_test_rows(
    predictions: pd.DataFrame,
    config: BinaryWeeklyWalkForwardConfig,
) -> pd.DataFrame:
    columns = ("test_name", "observed_statistic", "null_mean", "p_value", "permutations")
    if predictions.empty:
        return pd.DataFrame(columns=columns)
    y = predictions["target"].to_numpy(dtype=int)
    p = predictions["calibrated_probability"].to_numpy(dtype=float)
    baseline = predictions["weekly_frozen_baseline_probability"].to_numpy(dtype=float)
    observed_auc = float(roc_auc_score(y, p))
    observed_log_gain = float(log_loss(y, baseline, labels=[0, 1]) - log_loss(y, p, labels=[0, 1]))
    rng = np.random.default_rng(config.random_seed)
    week = predictions["test_week"].astype(str).to_numpy()
    auc_null = np.empty(config.null_permutations, dtype=float)
    log_null = np.empty(config.null_permutations, dtype=float)
    for iteration in range(config.null_permutations):
        shuffled = y.copy()
        for value in np.unique(week):
            indices = np.flatnonzero(week == value)
            shuffled[indices] = rng.permutation(shuffled[indices])
        auc_null[iteration] = roc_auc_score(shuffled, p)
        log_null[iteration] = log_loss(shuffled, baseline, labels=[0, 1]) - log_loss(
            shuffled, p, labels=[0, 1]
        )
    return pd.DataFrame(
        [
            {
                "test_name": "within_week_label_permutation_auc",
                "observed_statistic": observed_auc,
                "null_mean": float(auc_null.mean()),
                "p_value": float((1 + np.sum(auc_null >= observed_auc)) / (config.null_permutations + 1)),
                "permutations": config.null_permutations,
            },
            {
                "test_name": "within_week_label_permutation_log_loss_improvement",
                "observed_statistic": observed_log_gain,
                "null_mean": float(log_null.mean()),
                "p_value": float((1 + np.sum(log_null >= observed_log_gain)) / (config.null_permutations + 1)),
                "permutations": config.null_permutations,
            },
        ],
        columns=columns,
    )


def build_gate_rows(
    predictions: pd.DataFrame,
    metrics: pd.DataFrame,
    reliability: pd.DataFrame,
    null_tests: pd.DataFrame,
    weekly_metadata: pd.DataFrame,
    config: BinaryWeeklyWalkForwardConfig,
) -> pd.DataFrame:
    overall = metrics.loc[(metrics["slice"] == "overall") & (metrics["slice_value"] == "all")]
    values = dict(zip(overall["metric"], overall["value"]))
    high = reliability.loc[
        (reliability["kind"] == "threshold")
        & np.isclose(reliability["threshold"], config.gates.high_probability_threshold)
    ]
    high_row = high.iloc[0] if len(high) else pd.Series(dtype=float)
    skipped_fraction = (
        float((weekly_metadata["status"] == "SKIPPED").mean())
        if not weekly_metadata.empty
        else 1.0
    )
    null_p_values = (
        dict(zip(null_tests["test_name"], null_tests["p_value"]))
        if not null_tests.empty
        else {}
    )
    checks = (
        ("min_oos_rows", len(predictions), config.gates.min_oos_rows, ">="),
        (
            "max_skipped_oos_week_fraction",
            skipped_fraction,
            config.gates.max_skipped_oos_week_fraction,
            "<=",
        ),
        ("min_auc", values.get("auc", math.nan), config.gates.min_auc, ">="),
        ("min_log_loss_improvement", values.get("log_loss_improvement", math.nan), config.gates.min_log_loss_improvement, ">="),
        ("min_brier_improvement", values.get("brier_improvement", math.nan), config.gates.min_brier_improvement, ">="),
        ("max_ece", values.get("ece", math.nan), config.gates.max_ece, "<="),
        (
            "max_auc_permutation_p_value",
            null_p_values.get("within_week_label_permutation_auc", math.nan),
            config.gates.max_auc_permutation_p_value,
            "<=",
        ),
        (
            "max_log_loss_permutation_p_value",
            null_p_values.get(
                "within_week_label_permutation_log_loss_improvement", math.nan
            ),
            config.gates.max_log_loss_permutation_p_value,
            "<=",
        ),
        ("min_high_probability_rows", high_row.get("row_count", 0), config.gates.min_high_probability_rows, ">="),
        ("min_high_probability_observed_rate", high_row.get("observed_rate", math.nan), config.gates.min_high_probability_observed_rate, ">="),
        ("min_high_probability_wilson_lower_95", high_row.get("wilson_lower_95", math.nan), config.gates.min_high_probability_wilson_lower_95, ">="),
        ("max_high_probability_calibration_gap", high_row.get("absolute_gap", math.nan), config.gates.max_high_probability_calibration_gap, "<="),
    )
    rows = []
    for name, observed, required, operator in checks:
        finite = pd.notna(observed) and np.isfinite(float(observed))
        passed = finite and (
            float(observed) >= float(required) if operator == ">=" else float(observed) <= float(required)
        )
        rows.append(
            {
                "gate": name,
                "observed": observed,
                "operator": operator,
                "required": required,
                "status": "PASS" if passed else "FAIL",
            }
        )
    return pd.DataFrame(rows)


def _chronological_group_split(
    train: pd.DataFrame,
    config: BinaryWeeklyWalkForwardConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | str:
    split_group_column = config.split_group_column or config.group_column
    group_times = (
        train.groupby(split_group_column, sort=False)[config.snapshot_time_column]
        .min()
        .sort_values(kind="mergesort")
    )
    groups = group_times.index.to_numpy()
    fit_end = int(math.floor(len(groups) * config.fit_fraction))
    validation_end = int(math.floor(len(groups) * (config.fit_fraction + config.validation_fraction)))
    if fit_end <= 0 or validation_end <= fit_end or validation_end >= len(groups):
        return "insufficient_groups_for_60_20_20_split"
    fit_groups = set(groups[:fit_end])
    validation_groups = set(groups[fit_end:validation_end])
    calibration_groups = set(groups[validation_end:])
    return (
        train.loc[train[split_group_column].isin(fit_groups)].copy(),
        train.loc[train[split_group_column].isin(validation_groups)].copy(),
        train.loc[train[split_group_column].isin(calibration_groups)].copy(),
    )


def _split_support_reason(
    fit: pd.DataFrame,
    validation: pd.DataFrame,
    calibration: pd.DataFrame,
    config: BinaryWeeklyWalkForwardConfig,
) -> str:
    for name, part in (("fit", fit), ("validation", validation), ("calibration", calibration)):
        if len(part) < config.min_split_rows:
            return f"{name}_below_min_split_rows"
        counts = part[config.label_column].value_counts()
        if any(int(counts.get(value, 0)) < config.min_class_rows_per_split for value in (0, 1)):
            return f"{name}_below_min_class_rows"
    return ""


def _active_features(
    fit: pd.DataFrame,
    config: BinaryWeeklyWalkForwardConfig,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    numeric: list[str] = []
    categorical: list[str] = []
    dropped: list[str] = []
    for name in config.numeric_features:
        if fit[name].dropna().nunique() >= 2:
            numeric.append(name)
        else:
            dropped.append(name)
    for name in config.categorical_features:
        if fit[name].nunique(dropna=False) >= 2:
            categorical.append(name)
        else:
            dropped.append(name)
    return tuple(numeric), tuple(categorical), tuple(dropped)


def _model_matrix(
    frame: pd.DataFrame,
    numeric: Iterable[str],
    categorical: Iterable[str],
) -> pd.DataFrame:
    numeric_names = tuple(numeric)
    categorical_names = tuple(categorical)
    matrix = frame.loc[:, [*numeric_names, *categorical_names]].copy()
    for name in numeric_names:
        matrix[name] = matrix[name].astype(float)
    for name in categorical_names:
        matrix[name] = matrix[name].astype("string").fillna("__MISSING__").astype(str)
    return matrix


def _event_normalized_weights(frame: pd.DataFrame, group_column: str) -> np.ndarray:
    counts = frame.groupby(group_column)[group_column].transform("size").to_numpy(dtype=float)
    weights = 1.0 / counts
    return weights / weights.mean()


def _fit_isotonic_calibrator(
    raw: np.ndarray,
    target: np.ndarray,
    *,
    weights: np.ndarray,
    baseline: float,
    config: BinaryWeeklyWalkForwardConfig,
) -> IsotonicRegression:
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    if config.calibration_method == DIRECT_ISOTONIC_V1:
        calibrator.fit(raw, target, sample_weight=weights)
        return calibrator
    order = np.argsort(raw, kind="mergesort")
    partitions = np.array_split(order, min(config.isotonic_fit_bins, len(order)))
    bin_raw: list[float] = []
    bin_target: list[float] = []
    bin_weight: list[float] = []
    for indices in partitions:
        weight = float(weights[indices].sum())
        bin_raw.append(float(np.average(raw[indices], weights=weights[indices])))
        successes = float(np.dot(weights[indices], target[indices]))
        strength = config.isotonic_beta_prior_strength
        bin_target.append((successes + strength * baseline) / (weight + strength))
        bin_weight.append(weight)
    calibrator.fit(
        np.asarray(bin_raw),
        np.asarray(bin_target),
        sample_weight=np.asarray(bin_weight),
    )
    return calibrator


def _prediction_frame(
    test: pd.DataFrame,
    *,
    config: BinaryWeeklyWalkForwardConfig,
    test_week: str,
    freeze_ms: int,
    model_id: str,
    raw: np.ndarray,
    calibrated: np.ndarray,
    baseline: float,
) -> pd.DataFrame:
    result = pd.DataFrame(
        {
            "group": test[config.group_column].astype(str).to_numpy(),
            "symbol": test[config.symbol_column].astype(str).to_numpy(),
            "snapshot_time_ms": test[config.snapshot_time_column].to_numpy(dtype=np.int64),
            "feature_cutoff_time_ms": test[config.feature_cutoff_time_column].to_numpy(dtype=np.int64),
            "future_start_time_ms": test[config.future_start_time_column].to_numpy(dtype=np.int64),
            "resolution_time_ms": test[config.resolution_time_column].to_numpy(dtype=np.int64),
            "test_week": test_week,
            "weekly_model_freeze_time_ms": freeze_ms,
            "model_id": model_id,
            "target": test[config.label_column].to_numpy(dtype=np.int8),
            "raw_probability": raw,
            "calibrated_probability": calibrated,
            "weekly_frozen_baseline_probability": baseline,
        }
    )
    for name in config.breakdown_columns:
        result[name] = test[name].to_numpy()
    if (result["feature_cutoff_time_ms"] > result["snapshot_time_ms"]).any():
        raise AssertionError("prediction feature cutoff exceeded snapshot")
    if (result["weekly_model_freeze_time_ms"] > result["snapshot_time_ms"]).any():
        raise AssertionError("weekly model was frozen after an OOS prediction snapshot")
    return result


def _prediction_columns(config: BinaryWeeklyWalkForwardConfig) -> tuple[str, ...]:
    return (
        "group", "symbol", "snapshot_time_ms", "feature_cutoff_time_ms",
        "future_start_time_ms", "resolution_time_ms", "test_week",
        "weekly_model_freeze_time_ms", "model_id", "target", "raw_probability",
        "calibrated_probability", "weekly_frozen_baseline_probability",
        *config.breakdown_columns,
    )


def _metric_values(y: np.ndarray, p: np.ndarray, baseline: np.ndarray, bins: int) -> dict[str, float]:
    model_log_loss = float(log_loss(y, p, labels=[0, 1]))
    baseline_log_loss = float(log_loss(y, baseline, labels=[0, 1]))
    brier = float(np.mean((p - y) ** 2))
    baseline_brier = float(np.mean((baseline - y) ** 2))
    return {
        "positive_rate": float(np.mean(y)),
        "mean_probability": float(np.mean(p)),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else math.nan,
        "log_loss": model_log_loss,
        "baseline_log_loss": baseline_log_loss,
        "log_loss_improvement": baseline_log_loss - model_log_loss,
        "brier": brier,
        "baseline_brier": baseline_brier,
        "brier_improvement": baseline_brier - brier,
        "ece": _ece(y, p, bins),
    }


def _ece(y: np.ndarray, p: np.ndarray, bins: int) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    indices = np.minimum(np.searchsorted(edges, p, side="right") - 1, bins - 1)
    total = len(y)
    return float(
        sum(
            np.sum(indices == index) / total
            * abs(float(np.mean(p[indices == index])) - float(np.mean(y[indices == index])))
            for index in range(bins)
            if np.any(indices == index)
        )
    )


def _reliability_row(
    kind: str,
    mask: np.ndarray,
    p: np.ndarray,
    y: np.ndarray,
    lower: float,
    upper: float,
    threshold: float,
) -> dict[str, object]:
    count = int(np.sum(mask))
    if count == 0:
        return {
            "kind": kind, "lower_bound": lower, "upper_bound": upper,
            "threshold": threshold, "row_count": 0, "mean_probability": math.nan,
            "observed_rate": math.nan, "wilson_lower_95": math.nan, "absolute_gap": math.nan,
        }
    mean_probability = float(np.mean(p[mask]))
    observed = float(np.mean(y[mask]))
    return {
        "kind": kind,
        "lower_bound": lower,
        "upper_bound": upper,
        "threshold": threshold,
        "row_count": count,
        "mean_probability": mean_probability,
        "observed_rate": observed,
        "wilson_lower_95": _wilson_lower(int(np.sum(y[mask])), count),
        "absolute_gap": abs(mean_probability - observed),
    }


def _wilson_lower(successes: int, count: int, z: float = 1.959963984540054) -> float:
    if count <= 0:
        return math.nan
    rate = successes / count
    denominator = 1.0 + z * z / count
    centre = rate + z * z / (2.0 * count)
    margin = z * math.sqrt(rate * (1.0 - rate) / count + z * z / (4.0 * count * count))
    return max(0.0, (centre - margin) / denominator)


def _bool_series(series: pd.Series, *, name: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(series.dtype):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower()
    if not normalized.isin({"true", "false"}).all():
        raise BinaryProbabilityInputError(f"{name} must contain explicit booleans")
    return normalized == "true"


def _utc_week(timestamp_ms: int) -> str:
    parsed = datetime.fromtimestamp(int(timestamp_ms) / 1000.0, tz=timezone.utc)
    year, week, _ = parsed.isocalendar()
    return f"{year:04d}-W{week:02d}"


def _week_start_ms(week: str) -> int:
    parsed = datetime.strptime(f"{week}-1", "%G-W%V-%u").replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


__all__ = [
    "BinaryProbabilityInputError",
    "BinaryWalkForwardResult",
    "FrozenWeeklyBinaryModel",
    "build_binary_weekly_walk_forward",
    "build_gate_rows",
    "build_null_test_rows",
    "build_prediction_metrics",
    "build_reliability_rows",
    "validate_binary_probability_frame",
]
