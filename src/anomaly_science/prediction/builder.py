from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.isotonic import IsotonicRegression

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.features import AnomalyFeatureMatrixRow
from anomaly_science.contracts.labels import AnomalyOutcomeLabelRow, MISSING_FUTURE_SCENARIO
from anomaly_science.contracts.market import MarketDataContractError
from anomaly_science.contracts.prediction import (
    PREDICTED_SCENARIOS,
    PREDICTION_TEMPORAL_CONTRACT,
    CalibrationRow,
    FeatureImportanceRow,
    ModelMetadataRow,
    ModelTrainingDiagnosticRow,
    OosPredictionRow,
    PredictionMetricRow,
)
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.features.catalog import build_default_feature_catalog
from anomaly_science.features.matrix import load_anomaly_feature_matrix_csv
from anomaly_science.future import load_anomaly_state_1m_csv
from anomaly_science.labels import load_anomaly_outcome_labels_csv
from anomaly_science.prediction.config import WalkForwardPredictionConfig
from anomaly_science.strategy.registry import get_strategy

ONE_MINUTE_MS = 60_000
ONE_DAY_MS = 86_400_000
_EPS = 1e-15
STATE_MODEL_FEATURE_NAMES = (
    "minutes_since_event_start",
    "minutes_since_detection",
    "event_alive",
    "time_since_running_high_minutes",
    "current_return_from_start",
    "distance_to_running_high",
    "distance_to_running_low",
    "distance_to_structural_low",
    "distance_to_structural_high",
    "missing_structural_low",
    "missing_structural_high",
)
FEATURE_MATRIX_MODEL_FEATURE_NAMES = tuple(
    f"feature_matrix.{row.feature_name}"
    for row in build_default_feature_catalog()
    if row.source_artifact == "anomaly_feature_matrix.csv" and row.is_model_feature and row.dtype != "str"
)
MODEL_FEATURE_NAMES = STATE_MODEL_FEATURE_NAMES


class PredictionInputError(ValueError):
    """Raised when state/label artifacts cannot produce prediction inputs."""


class PredictionArtifactError(ValueError):
    """Raised when prediction artifacts violate strict schemas."""


@dataclass(frozen=True, slots=True)
class WalkForwardPredictionResult:
    predictions: tuple[OosPredictionRow, ...]
    model_metadata: tuple[ModelMetadataRow, ...]
    feature_importance: tuple[FeatureImportanceRow, ...]
    model_training_diagnostics: tuple[ModelTrainingDiagnosticRow, ...]


@dataclass(frozen=True, slots=True)
class PredictionInputRow:
    state: AnomalyState1mRow
    label: AnomalyOutcomeLabelRow
    features: AnomalyFeatureMatrixRow | None = None

    @property
    def target_scenario_15m(self) -> str:
        return self.label.scenario_15m

    @property
    def target_scenario_30m(self) -> str:
        return self.label.scenario_30m

    @property
    def target_scenario_60m(self) -> str:
        return self.label.scenario_60m

    @property
    def target_scenario_120m(self) -> str:
        return self.label.scenario_120m


def load_prediction_inputs(
    *,
    state_path: str | Path,
    labels_path: str | Path,
    feature_matrix_path: str | Path | None = None,
) -> tuple[PredictionInputRow, ...]:
    """Load prediction inputs through strict state and label schema boundaries."""
    return build_prediction_inputs(
        state_rows=load_anomaly_state_1m_csv(state_path),
        label_rows=load_anomaly_outcome_labels_csv(labels_path),
        feature_rows=None if feature_matrix_path is None else load_anomaly_feature_matrix_csv(feature_matrix_path),
    )


def build_prediction_inputs(
    *,
    state_rows: Sequence[AnomalyState1mRow] | Iterable[AnomalyState1mRow],
    label_rows: Sequence[AnomalyOutcomeLabelRow] | Iterable[AnomalyOutcomeLabelRow],
    feature_rows: Sequence[AnomalyFeatureMatrixRow] | Iterable[AnomalyFeatureMatrixRow] | None = None,
) -> tuple[PredictionInputRow, ...]:
    states = tuple(state_rows)
    labels = tuple(label_rows)
    features = None if feature_rows is None else tuple(feature_rows)
    state_by_key = _unique_by_join_key(states, artifact_name="anomaly_state_1m.csv")
    label_by_key = _unique_by_join_key(labels, artifact_name="anomaly_outcome_labels.csv")
    feature_by_key = None if features is None else _unique_by_join_key(features, artifact_name="anomaly_feature_matrix.csv")

    state_keys = set(state_by_key)
    label_keys = set(label_by_key)
    if state_keys != label_keys:
        missing_label = sorted(state_keys - label_keys)[:5]
        orphan_label = sorted(label_keys - state_keys)[:5]
        raise PredictionInputError(
            "state/label prediction join must be one-to-one on "
            "event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms; "
            f"missing_label={missing_label}, orphan_label={orphan_label}"
        )
    if feature_by_key is not None and set(feature_by_key) != state_keys:
        missing_feature = sorted(state_keys - set(feature_by_key))[:5]
        orphan_feature = sorted(set(feature_by_key) - state_keys)[:5]
        raise PredictionInputError(
            "state/feature prediction join must be one-to-one on "
            "event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms; "
            f"missing_feature={missing_feature}, orphan_feature={orphan_feature}"
        )

    rows: list[PredictionInputRow] = []
    for key in sorted(state_keys):
        state = state_by_key[key]
        label = label_by_key[key]
        feature = None if feature_by_key is None else feature_by_key[key]
        _enforce_prediction_input_temporal_contract(state=state, label=label, feature=feature)
        rows.append(PredictionInputRow(state=state, label=label, features=feature))
    return tuple(rows)


def build_walk_forward_predictions(
    *,
    inputs: Sequence[PredictionInputRow] | Iterable[PredictionInputRow],
    config: WalkForwardPredictionConfig | None = None,
) -> tuple[OosPredictionRow, ...]:
    return build_walk_forward_prediction_result(inputs=inputs, config=config).predictions


def build_walk_forward_prediction_result(
    *,
    inputs: Sequence[PredictionInputRow] | Iterable[PredictionInputRow],
    config: WalkForwardPredictionConfig | None = None,
) -> WalkForwardPredictionResult:
    cfg = config or WalkForwardPredictionConfig()
    _validate_strategy_horizon(config=cfg)
    usable_rows = [row for row in inputs if _target_for_horizon(row.label, cfg.target_horizon_minutes) != MISSING_FUTURE_SCENARIO]
    usable_rows.sort(key=lambda item: (item.state.snapshot_time_ms, item.state.symbol, item.state.event_id))
    rows_by_test_week: dict[str, list[PredictionInputRow]] = defaultdict(list)
    for row in usable_rows:
        rows_by_test_week[_utc_week(row.state.snapshot_time_ms)].append(row)

    predictions: list[OosPredictionRow] = []
    metadata_rows: list[ModelMetadataRow] = []
    importance_rows: list[FeatureImportanceRow] = []
    diagnostic_rows: list[ModelTrainingDiagnosticRow] = []
    for test_week in sorted(rows_by_test_week):
        weekly_model_freeze_time_ms = _week_start_ms(test_week)
        train_cutoff_time_ms = weekly_model_freeze_time_ms - cfg.purge_horizon_minutes * ONE_MINUTE_MS
        train_rows = [row for row in usable_rows if row.state.snapshot_time_ms <= train_cutoff_time_ms]
        weekly_model_prefix = f"weekly_freeze={test_week}:{weekly_model_freeze_time_ms}"
        frozen_model_key = f"{weekly_model_prefix}|catboost_isotonic"
        diagnostic = _weekly_training_diagnostic(
            rows=train_rows,
            config=cfg,
            prediction_version=cfg.prediction_version,
            model_key=frozen_model_key,
            test_week=test_week,
            weekly_model_freeze_time_ms=weekly_model_freeze_time_ms,
            train_cutoff_time_ms=train_cutoff_time_ms,
        )
        diagnostic_rows.append(diagnostic)
        if diagnostic.status != "TRAINED":
            continue
        model = _CatBoostIsotonicModel.fit(rows=train_rows, config=cfg)
        if model is None:
            continue
        metadata_rows.append(
            model.metadata_row(
                prediction_version=cfg.prediction_version,
                model_key=frozen_model_key,
                weekly_model_freeze_time_ms=weekly_model_freeze_time_ms,
                train_cutoff_time_ms=train_cutoff_time_ms,
            )
        )
        importance_rows.extend(
            model.feature_importance_rows(
                prediction_version=cfg.prediction_version,
                model_key=frozen_model_key,
            )
        )
        for test_row in sorted(rows_by_test_week[test_week], key=lambda item: (item.state.snapshot_time_ms, item.state.symbol, item.state.event_id)):
            target_scenario = _target_for_horizon(test_row.label, cfg.target_horizon_minutes)
            if target_scenario == MISSING_FUTURE_SCENARIO:
                continue
            raw_probabilities, probabilities, model_key, group_count = model.predict(test_row)
            predicted_scenario, confidence = _predicted_scenario(probabilities)
            test_day = _utc_day(test_row.state.snapshot_time_ms)
            predictions.append(
                OosPredictionRow(
                    prediction_version=cfg.prediction_version,
                    event_id=test_row.state.event_id,
                    symbol=test_row.state.symbol,
                    snapshot_time_ms=test_row.state.snapshot_time_ms,
                    feature_cutoff_time_ms=test_row.state.feature_cutoff_time_ms,
                    future_start_time_ms=test_row.label.future_start_time_ms,
                    target_horizon_minutes=cfg.target_horizon_minutes,
                    target_scenario=target_scenario,
                    test_day=test_day,
                    train_cutoff_time_ms=train_cutoff_time_ms,
                    model_family=cfg.model_family,
                    model_key=f"{weekly_model_prefix}|{model_key}",
                    model_train_row_count=model.train_row_count,
                    model_group_row_count=group_count,
                    raw_p_long_continuation=raw_probabilities["long_continuation"],
                    raw_p_short_fade=raw_probabilities["short_fade"],
                    raw_p_static_or_chop=raw_probabilities["static_or_chop"],
                    raw_p_unclear=raw_probabilities["unclear"],
                    p_long_continuation=probabilities["long_continuation"],
                    p_short_fade=probabilities["short_fade"],
                    p_static_or_chop=probabilities["static_or_chop"],
                    p_unclear=probabilities["unclear"],
                    predicted_scenario=predicted_scenario,
                    prediction_confidence=confidence,
                    temporal_contract=PREDICTION_TEMPORAL_CONTRACT,
                )
            )
    return WalkForwardPredictionResult(
        predictions=tuple(predictions),
        model_metadata=tuple(metadata_rows),
        feature_importance=tuple(importance_rows),
        model_training_diagnostics=tuple(diagnostic_rows),
    )


def build_calibration_rows(
    *,
    predictions: Sequence[OosPredictionRow] | Iterable[OosPredictionRow],
) -> tuple[CalibrationRow, ...]:
    rows = tuple(predictions)
    grouped: dict[tuple[str, str], list[OosPredictionRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.predicted_scenario, _confidence_bucket(row.prediction_confidence))].append(row)

    result: list[CalibrationRow] = []
    for (predicted_scenario, confidence_bucket), group in sorted(grouped.items()):
        result.append(
            CalibrationRow(
                prediction_version=group[0].prediction_version,
                target_horizon_minutes=group[0].target_horizon_minutes,
                predicted_scenario=predicted_scenario,
                confidence_bucket=confidence_bucket,
                row_count=len(group),
                mean_confidence=_mean(row.prediction_confidence for row in group),
                empirical_accuracy=_mean(1.0 if row.predicted_scenario == row.target_scenario else 0.0 for row in group),
                multiclass_brier=_mean(_brier_score(row) for row in group),
                log_loss=_mean(_log_loss(row) for row in group),
            )
        )
    return tuple(result)


def build_prediction_metric_rows(
    *,
    inputs: Sequence[PredictionInputRow] | Iterable[PredictionInputRow],
    predictions: Sequence[OosPredictionRow] | Iterable[OosPredictionRow],
    config: WalkForwardPredictionConfig | None = None,
) -> tuple[PredictionMetricRow, ...]:
    cfg = config or WalkForwardPredictionConfig()
    input_rows = tuple(inputs)
    prediction_rows = tuple(predictions)
    available_rows = [row for row in input_rows if _target_for_horizon(row.label, cfg.target_horizon_minutes) != MISSING_FUTURE_SCENARIO]
    test_weeks = sorted({_utc_week(row.state.snapshot_time_ms) for row in available_rows})
    trainless_test_rows = _count_trainless_test_rows(available_rows=available_rows, test_weeks=test_weeks, config=cfg)
    metrics: list[PredictionMetricRow] = []

    def add(name: str, value: str | float | int, row_count: int, notes: str) -> None:
        metrics.append(
            PredictionMetricRow(
                prediction_version=cfg.prediction_version,
                target_horizon_minutes=cfg.target_horizon_minutes,
                metric_name=name,
                metric_value=_metric_value(value),
                row_count=row_count,
                notes=notes,
            )
        )

    add("input_rows", len(input_rows), len(input_rows), "state/label rows accepted through strict schema boundaries")
    add(
        "available_label_rows",
        len(available_rows),
        len(available_rows),
        "rows with non-missing descriptive target scenario for the selected horizon",
    )
    add(
        "missing_future_rows_excluded",
        len(input_rows) - len(available_rows),
        len(input_rows) - len(available_rows),
        "missing_future is treated as data condition and excluded from probability fitting/evaluation",
    )
    add(
        "oos_prediction_rows",
        len(prediction_rows),
        len(prediction_rows),
        "weekly frozen-model OOS rows emitted after train-size and purge checks",
    )
    add("test_day_count", len({row.test_day for row in prediction_rows}), len(prediction_rows), "unique evaluated OOS days")
    add(
        "weekly_model_count",
        len({row.model_key.split("|", 1)[0] for row in prediction_rows}),
        len(prediction_rows),
        "unique frozen weekly model identifiers used for OOS rows",
    )
    add("trainless_test_rows_skipped", trainless_test_rows, trainless_test_rows, "test rows skipped because earlier purged train set was too small")
    if prediction_rows:
        add("overall_accuracy", _mean(1.0 if row.predicted_scenario == row.target_scenario else 0.0 for row in prediction_rows), len(prediction_rows), "argmax scenario accuracy; not a trading metric")
        add("multiclass_brier", _mean(_brier_score(row) for row in prediction_rows), len(prediction_rows), "mean multiclass Brier score over OOS rows")
        add("log_loss", _mean(_log_loss(row) for row in prediction_rows), len(prediction_rows), "mean multiclass log loss over OOS rows")
        add("expected_calibration_error", _expected_calibration_error(prediction_rows), len(prediction_rows), "confidence-bucket expected calibration error over OOS rows")
        add("mean_prediction_confidence", _mean(row.prediction_confidence for row in prediction_rows), len(prediction_rows), "mean max-class probability")
        add("max_probability_sum_error", max(abs(_probability_sum(row) - 1.0) for row in prediction_rows), len(prediction_rows), "strict probability normalization audit")
        for scenario in PREDICTED_SCENARIOS:
            add(
                f"target_share_{scenario}",
                _mean(1.0 if row.target_scenario == scenario else 0.0 for row in prediction_rows),
                len(prediction_rows),
                "OOS target class share for selected horizon",
            )
    return tuple(metrics)


def oos_prediction_rows_to_artifact(rows: Sequence[OosPredictionRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def calibration_rows_to_artifact(rows: Sequence[CalibrationRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def prediction_metric_rows_to_artifact(rows: Sequence[PredictionMetricRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def model_metadata_rows_to_artifact(rows: Sequence[ModelMetadataRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def feature_importance_rows_to_artifact(rows: Sequence[FeatureImportanceRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def model_training_diagnostic_rows_to_artifact(rows: Sequence[ModelTrainingDiagnosticRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def load_anomaly_oos_predictions_csv(path: str | Path) -> tuple[OosPredictionRow, ...]:
    return tuple(_load_prediction_artifact(path=path, schema_name="anomaly_oos_predictions.csv", row_builder=_oos_prediction_from_mapping))


def load_anomaly_calibration_csv(path: str | Path) -> tuple[CalibrationRow, ...]:
    return tuple(_load_prediction_artifact(path=path, schema_name="anomaly_calibration.csv", row_builder=_calibration_from_mapping))


def load_anomaly_prediction_metrics_csv(path: str | Path) -> tuple[PredictionMetricRow, ...]:
    return tuple(_load_prediction_artifact(path=path, schema_name="anomaly_prediction_metrics.csv", row_builder=_metric_from_mapping))


class _EmpiricalStateBinModel:
    def __init__(
        self,
        *,
        config: WalkForwardPredictionConfig,
        train_row_count: int,
        global_counts: Counter[str],
        exact_counts: dict[str, Counter[str]],
        partial_counts: dict[str, Counter[str]],
    ) -> None:
        self._config = config
        self.train_row_count = train_row_count
        self._global_counts = global_counts
        self._exact_counts = exact_counts
        self._partial_counts = partial_counts
        self._global_prior = _smoothed_distribution(global_counts, prior=None, smoothing_strength=0.0)

    @classmethod
    def fit(cls, *, rows: Sequence[PredictionInputRow], config: WalkForwardPredictionConfig) -> _EmpiricalStateBinModel:
        global_counts: Counter[str] = Counter()
        exact_counts: dict[str, Counter[str]] = defaultdict(Counter)
        partial_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            target = _target_for_horizon(row.label, config.target_horizon_minutes)
            if target == MISSING_FUTURE_SCENARIO:
                continue
            bins = _state_bins(row.state)
            exact_key = _exact_model_key(bins)
            partial_key = _partial_model_key(bins)
            global_counts[target] += 1
            exact_counts[exact_key][target] += 1
            partial_counts[partial_key][target] += 1
        return cls(
            config=config,
            train_row_count=sum(global_counts.values()),
            global_counts=global_counts,
            exact_counts=dict(exact_counts),
            partial_counts=dict(partial_counts),
        )

    def predict(self, state: AnomalyState1mRow) -> tuple[dict[str, float], str, int]:
        bins = _state_bins(state)
        exact_key = _exact_model_key(bins)
        partial_key = _partial_model_key(bins)
        exact = self._exact_counts.get(exact_key)
        if exact is not None and sum(exact.values()) >= self._config.min_group_rows:
            return (
                _smoothed_distribution(exact, prior=self._global_prior, smoothing_strength=self._config.smoothing_strength),
                exact_key,
                sum(exact.values()),
            )
        partial = self._partial_counts.get(partial_key)
        if partial is not None and sum(partial.values()) >= self._config.min_group_rows:
            return (
                _smoothed_distribution(partial, prior=self._global_prior, smoothing_strength=self._config.smoothing_strength),
                partial_key,
                sum(partial.values()),
            )
        return (dict(self._global_prior), "global_prior", sum(self._global_counts.values()))


class _CatBoostIsotonicModel:
    def __init__(
        self,
        *,
        config: WalkForwardPredictionConfig,
        model: CatBoostClassifier,
        calibrators: dict[str, IsotonicRegression],
        train_row_count: int,
        fit_row_count: int,
        validation_row_count: int,
        calibration_row_count: int,
        feature_names: tuple[str, ...],
    ) -> None:
        self._config = config
        self._model = model
        self._calibrators = calibrators
        self.train_row_count = train_row_count
        self.fit_row_count = fit_row_count
        self.validation_row_count = validation_row_count
        self.calibration_row_count = calibration_row_count
        self.feature_names = feature_names

    @classmethod
    def fit(cls, *, rows: Sequence[PredictionInputRow], config: WalkForwardPredictionConfig) -> _CatBoostIsotonicModel | None:
        ordered = sorted(rows, key=lambda row: (row.state.snapshot_time_ms, row.state.symbol, row.state.event_id))
        if len(ordered) < config.min_train_rows:
            return None
        fit_rows, validation_rows, calibration_rows = _train_validation_calibration_split(ordered)
        fit_targets = _targets(fit_rows, config)
        validation_targets = _targets(validation_rows, config)
        calibration_targets = _targets(calibration_rows, config)
        if len(set(fit_targets)) < 2:
            return None
        if len(set(validation_targets)) < 2:
            return None
        if len(calibration_rows) < len(PREDICTED_SCENARIOS):
            return None
        if set(calibration_targets) != set(PREDICTED_SCENARIOS):
            return None
        feature_names = _input_feature_names(ordered, config=config)

        model = CatBoostClassifier(
            loss_function="MultiClass",
            class_names=list(PREDICTED_SCENARIOS),
            iterations=config.catboost_iterations,
            depth=config.catboost_depth,
            learning_rate=config.catboost_learning_rate,
            random_seed=config.random_seed,
            verbose=False,
            allow_writing_files=False,
        )
        eval_set = (_feature_matrix(validation_rows, feature_names=feature_names), validation_targets)
        model.fit(
            _feature_matrix(fit_rows, feature_names=feature_names),
            fit_targets,
            eval_set=eval_set,
            use_best_model=True,
            early_stopping_rounds=20,
        )

        raw_calibration = _raw_probability_matrix(model, calibration_rows, feature_names=feature_names)
        calibrators: dict[str, IsotonicRegression] = {}
        for scenario_index, scenario in enumerate(PREDICTED_SCENARIOS):
            binary_targets = np.array([1.0 if target == scenario else 0.0 for target in calibration_targets], dtype=float)
            if len(set(binary_targets.tolist())) < 2:
                return None
            calibrator = IsotonicRegression(out_of_bounds="clip")
            calibrator.fit(raw_calibration[:, scenario_index], binary_targets)
            calibrators[scenario] = calibrator
        return cls(
            config=config,
            model=model,
            calibrators=calibrators,
            train_row_count=len(ordered),
            fit_row_count=len(fit_rows),
            validation_row_count=len(validation_rows),
            calibration_row_count=len(calibration_rows),
            feature_names=feature_names,
        )

    def predict(self, row: PredictionInputRow) -> tuple[dict[str, float], dict[str, float], str, int]:
        raw_vector = self._model.predict_proba(_feature_matrix([row], feature_names=self.feature_names))[0]
        raw = _probability_dict(raw_vector)
        calibrated_vector = np.array(
            [self._calibrators[scenario].predict([raw[scenario]])[0] for scenario in PREDICTED_SCENARIOS],
            dtype=float,
        )
        calibrated = _probability_dict(_normalize_probability_array(calibrated_vector))
        return raw, calibrated, "catboost_isotonic", self.train_row_count

    def metadata_row(
        self,
        *,
        prediction_version: str,
        model_key: str,
        weekly_model_freeze_time_ms: int,
        train_cutoff_time_ms: int,
    ) -> ModelMetadataRow:
        return ModelMetadataRow(
            prediction_version=prediction_version,
            model_key=model_key,
            model_family=self._config.model_family,
            weekly_model_freeze_time_ms=weekly_model_freeze_time_ms,
            train_cutoff_time_ms=train_cutoff_time_ms,
            train_row_count=self.train_row_count,
            fit_row_count=self.fit_row_count,
            validation_row_count=self.validation_row_count,
            calibration_row_count=self.calibration_row_count,
            best_iteration=self._best_iteration(),
            class_order=",".join(PREDICTED_SCENARIOS),
            model_feature_names=",".join(self.feature_names),
            calibration_method="one_vs_rest_isotonic_regression_on_train_calibration_split",
        )

    def _best_iteration(self) -> int:
        best_iteration = self._model.get_best_iteration()
        if best_iteration is None:
            return int(self._config.catboost_iterations)
        return int(best_iteration)

    def feature_importance_rows(self, *, prediction_version: str, model_key: str) -> tuple[FeatureImportanceRow, ...]:
        importances = [float(value) for value in self._model.get_feature_importance()]
        ranked = sorted(zip(self.feature_names, importances, strict=True), key=lambda item: item[1], reverse=True)
        return tuple(
            FeatureImportanceRow(
                prediction_version=prediction_version,
                model_key=model_key,
                feature_name=feature_name,
                feature_importance=importance,
                rank=index + 1,
            )
            for index, (feature_name, importance) in enumerate(ranked)
        )


def _load_prediction_artifact(*, path: str | Path, schema_name: str, row_builder: object) -> tuple[object, ...]:
    artifact_path = Path(path)
    if not artifact_path.exists():
        raise PredictionArtifactError(f"prediction artifact is missing: {artifact_path}")
    frame = pd.read_csv(artifact_path)
    schema = get_artifact_schema(schema_name)
    expected_columns = list(schema.required_columns)
    actual_columns = list(frame.columns)
    if actual_columns != expected_columns:
        raise PredictionArtifactError(f"{schema_name} columns must match {expected_columns}, got {actual_columns}")
    rows: list[object] = []
    for row_index, row in frame.iterrows():
        try:
            rows.append(row_builder(row))  # type: ignore[operator]
        except (TypeError, ValueError, MarketDataContractError) as exc:
            raise PredictionArtifactError(f"invalid {schema_name} row {row_index}: {exc}") from exc
    return tuple(rows)


def _oos_prediction_from_mapping(row: Mapping[str, object]) -> OosPredictionRow:
    return OosPredictionRow(
        prediction_version=_required_str(row, "prediction_version"),
        event_id=_required_str(row, "event_id"),
        symbol=_required_str(row, "symbol"),
        snapshot_time_ms=_required_int(row, "snapshot_time_ms"),
        feature_cutoff_time_ms=_required_int(row, "feature_cutoff_time_ms"),
        future_start_time_ms=_required_int(row, "future_start_time_ms"),
        target_horizon_minutes=_required_int(row, "target_horizon_minutes"),
        target_scenario=_required_str(row, "target_scenario"),
        test_day=_required_str(row, "test_day"),
        train_cutoff_time_ms=_required_int(row, "train_cutoff_time_ms"),
        model_family=_required_str(row, "model_family"),
        model_key=_required_str(row, "model_key"),
        model_train_row_count=_required_int(row, "model_train_row_count"),
        model_group_row_count=_required_int(row, "model_group_row_count"),
        raw_p_long_continuation=_required_float(row, "raw_p_long_continuation"),
        raw_p_short_fade=_required_float(row, "raw_p_short_fade"),
        raw_p_static_or_chop=_required_float(row, "raw_p_static_or_chop"),
        raw_p_unclear=_required_float(row, "raw_p_unclear"),
        p_long_continuation=_required_float(row, "p_long_continuation"),
        p_short_fade=_required_float(row, "p_short_fade"),
        p_static_or_chop=_required_float(row, "p_static_or_chop"),
        p_unclear=_required_float(row, "p_unclear"),
        predicted_scenario=_required_str(row, "predicted_scenario"),
        prediction_confidence=_required_float(row, "prediction_confidence"),
        temporal_contract=_required_str(row, "temporal_contract"),
    )


def _calibration_from_mapping(row: Mapping[str, object]) -> CalibrationRow:
    return CalibrationRow(
        prediction_version=_required_str(row, "prediction_version"),
        target_horizon_minutes=_required_int(row, "target_horizon_minutes"),
        predicted_scenario=_required_str(row, "predicted_scenario"),
        confidence_bucket=_required_str(row, "confidence_bucket"),
        row_count=_required_int(row, "row_count"),
        mean_confidence=_required_float(row, "mean_confidence"),
        empirical_accuracy=_required_float(row, "empirical_accuracy"),
        multiclass_brier=_required_float(row, "multiclass_brier"),
        log_loss=_required_float(row, "log_loss"),
    )


def _metric_from_mapping(row: Mapping[str, object]) -> PredictionMetricRow:
    return PredictionMetricRow(
        prediction_version=_required_str(row, "prediction_version"),
        target_horizon_minutes=_required_int(row, "target_horizon_minutes"),
        metric_name=_required_str(row, "metric_name"),
        metric_value=_required_str(row, "metric_value"),
        row_count=_required_int(row, "row_count"),
        notes=_required_str(row, "notes"),
    )


def _target_for_horizon(label: AnomalyOutcomeLabelRow, horizon_minutes: int) -> str:
    if horizon_minutes == 15:
        return label.scenario_15m
    if horizon_minutes == 30:
        return label.scenario_30m
    if horizon_minutes == 60:
        return label.scenario_60m
    if horizon_minutes == 120:
        return label.scenario_120m
    raise ValueError(f"unsupported prediction target horizon: {horizon_minutes}")


def _validate_strategy_horizon(*, config: WalkForwardPredictionConfig) -> None:
    strategy = get_strategy(config.strategy_version)
    if strategy.metadata.horizon_minutes != config.target_horizon_minutes:
        raise PredictionInputError(
            f"prediction target_horizon_minutes={config.target_horizon_minutes} "
            f"does not match strategy horizon {strategy.metadata.horizon_minutes}"
        )


def _unique_by_join_key(
    rows: Iterable[AnomalyState1mRow] | Iterable[AnomalyOutcomeLabelRow] | Iterable[AnomalyFeatureMatrixRow],
    *,
    artifact_name: str,
) -> dict[tuple[str, str, int, int], object]:
    result: dict[tuple[str, str, int, int], object] = {}
    for row in rows:
        key = _join_key(row)
        if key in result:
            raise PredictionInputError(f"duplicate prediction join key in {artifact_name}: {key}")
        result[key] = row
    return result


def _join_key(row: AnomalyState1mRow | AnomalyOutcomeLabelRow | AnomalyFeatureMatrixRow) -> tuple[str, str, int, int]:
    return (row.event_id, row.symbol, row.snapshot_time_ms, row.feature_cutoff_time_ms)


def _enforce_prediction_input_temporal_contract(
    *,
    state: AnomalyState1mRow,
    label: AnomalyOutcomeLabelRow,
    feature: AnomalyFeatureMatrixRow | None,
) -> None:
    if state.snapshot_time_ms != label.snapshot_time_ms:
        raise MarketDataContractError("prediction join requires equal state/label snapshot_time_ms")
    if state.feature_cutoff_time_ms != label.feature_cutoff_time_ms:
        raise MarketDataContractError("prediction join requires equal state/label feature_cutoff_time_ms")
    if state.feature_cutoff_time_ms > state.snapshot_time_ms:
        raise MarketDataContractError("prediction state feature_cutoff_time_ms must be <= snapshot_time_ms")
    if label.feature_cutoff_time_ms > label.snapshot_time_ms:
        raise MarketDataContractError("prediction label feature_cutoff_time_ms must be <= snapshot_time_ms")
    if label.future_start_time_ms <= state.snapshot_time_ms:
        raise MarketDataContractError("prediction label future_start_time_ms must be > state snapshot_time_ms")
    if feature is not None:
        if feature.snapshot_time_ms != state.snapshot_time_ms:
            raise MarketDataContractError("prediction feature join requires equal feature/state snapshot_time_ms")
        if feature.feature_cutoff_time_ms != state.feature_cutoff_time_ms:
            raise MarketDataContractError("prediction feature join requires equal feature/state feature_cutoff_time_ms")
        if feature.feature_cutoff_time_ms > feature.snapshot_time_ms:
            raise MarketDataContractError("prediction feature feature_cutoff_time_ms must be <= snapshot_time_ms")


def _state_bins(state: AnomalyState1mRow) -> dict[str, str]:
    return {
        "maturity": _integer_bucket(state.minutes_since_detection, ((0, 2, "detect_0_2m"), (3, 5, "detect_3_5m"), (6, 10, "detect_6_10m")), "detect_11m_plus"),
        "event_age": _integer_bucket(state.minutes_since_event_start, ((0, 5, "age_0_5m"), (6, 15, "age_6_15m"), (16, 30, "age_16_30m")), "age_31m_plus"),
        "return": _float_bucket(state.current_return_from_start, ((-0.02, "return_deep_negative"), (-0.005, "return_negative"), (0.005, "return_flat"), (0.02, "return_positive")), "return_strong_positive"),
        "distance_high": _float_bucket(state.distance_to_running_high, ((-0.03, "far_below_high"), (-0.01, "below_high"), (-0.001, "near_high"), (0.001, "at_high")), "above_high"),
        "distance_low": _float_bucket(state.distance_to_running_low, ((0.001, "at_low"), (0.01, "near_low"), (0.03, "above_low")), "far_above_low"),
        "time_since_high": _integer_bucket(state.time_since_running_high_minutes, ((0, 0, "just_made_high"), (1, 3, "high_1_3m_ago"), (4, 10, "high_4_10m_ago")), "high_11m_plus_ago"),
        "alive": "alive" if state.event_alive else "not_alive",
    }


def _train_validation_calibration_split(
    rows: Sequence[PredictionInputRow],
) -> tuple[tuple[PredictionInputRow, ...], tuple[PredictionInputRow, ...], tuple[PredictionInputRow, ...]]:
    first_cut = max(1, int(len(rows) * 0.60))
    second_cut = max(first_cut + 1, int(len(rows) * 0.80))
    second_cut = min(second_cut, len(rows) - 1)
    return tuple(rows[:first_cut]), tuple(rows[first_cut:second_cut]), tuple(rows[second_cut:])


def _weekly_training_diagnostic(
    *,
    rows: Sequence[PredictionInputRow],
    config: WalkForwardPredictionConfig,
    prediction_version: str,
    model_key: str,
    test_week: str,
    weekly_model_freeze_time_ms: int,
    train_cutoff_time_ms: int,
) -> ModelTrainingDiagnosticRow:
    fit_rows: tuple[PredictionInputRow, ...] = ()
    validation_rows: tuple[PredictionInputRow, ...] = ()
    calibration_rows: tuple[PredictionInputRow, ...] = ()
    fit_targets: list[str] = []
    validation_targets: list[str] = []
    calibration_targets: list[str] = []
    reason = "trained"
    status = "TRAINED"
    if len(rows) < config.min_train_rows:
        status = "SKIPPED"
        reason = "insufficient_train_rows"
    else:
        fit_rows, validation_rows, calibration_rows = _train_validation_calibration_split(rows)
        fit_targets = _targets(fit_rows, config)
        validation_targets = _targets(validation_rows, config)
        calibration_targets = _targets(calibration_rows, config)
        if len(set(fit_targets)) < 2:
            status = "SKIPPED"
            reason = "fit_split_has_less_than_two_classes"
        elif len(set(validation_targets)) < 2:
            status = "SKIPPED"
            reason = "validation_split_has_less_than_two_classes"
        elif len(calibration_rows) < len(PREDICTED_SCENARIOS):
            status = "SKIPPED"
            reason = "calibration_split_has_too_few_rows"
        elif set(calibration_targets) != set(PREDICTED_SCENARIOS):
            status = "SKIPPED"
            reason = "calibration_split_missing_target_class"
    return ModelTrainingDiagnosticRow(
        prediction_version=prediction_version,
        model_key=model_key,
        test_week=test_week,
        weekly_model_freeze_time_ms=weekly_model_freeze_time_ms,
        train_cutoff_time_ms=train_cutoff_time_ms,
        train_row_count=len(rows),
        fit_row_count=len(fit_rows),
        validation_row_count=len(validation_rows),
        calibration_row_count=len(calibration_rows),
        fit_class_count=len(set(fit_targets)),
        validation_class_count=len(set(validation_targets)),
        calibration_class_count=len(set(calibration_targets)),
        status=status,
        reason=reason,
    )


def _targets(rows: Sequence[PredictionInputRow], config: WalkForwardPredictionConfig) -> list[str]:
    return [_target_for_horizon(row.label, config.target_horizon_minutes) for row in rows]


def _input_feature_names(rows: Sequence[PredictionInputRow], *, config: WalkForwardPredictionConfig) -> tuple[str, ...]:
    has_feature_matrix = {row.features is not None for row in rows}
    if has_feature_matrix == {True}:
        names = (*STATE_MODEL_FEATURE_NAMES, *FEATURE_MATRIX_MODEL_FEATURE_NAMES)
    if has_feature_matrix == {False}:
        names = STATE_MODEL_FEATURE_NAMES
    if has_feature_matrix not in ({True}, {False}):
        raise PredictionInputError("prediction rows must not mix feature-matrix and state-only inputs")
    if config.excluded_model_feature_prefixes:
        names = tuple(
            name
            for name in names
            if not any(name.startswith(prefix) for prefix in config.excluded_model_feature_prefixes)
        )
    if not names:
        raise PredictionInputError("model feature set is empty after exclusions")
    return names


def _feature_matrix(rows: Sequence[PredictionInputRow], *, feature_names: tuple[str, ...]) -> np.ndarray:
    return np.array([_feature_vector(row, feature_names=feature_names) for row in rows], dtype=float)


def _feature_vector(row: PredictionInputRow, *, feature_names: tuple[str, ...]) -> list[float]:
    state_values = _state_feature_values(row.state)
    values: list[float] = []
    for name in feature_names:
        if name in state_values:
            values.append(state_values[name])
            continue
        if not name.startswith("feature_matrix."):
            raise PredictionInputError(f"unknown model feature name: {name}")
        if row.features is None:
            raise PredictionInputError(f"missing anomaly_feature_matrix.csv row for model feature: {name}")
        values.append(_feature_matrix_value(row.features, name.removeprefix("feature_matrix.")))
    return values


def _state_feature_values(state: AnomalyState1mRow) -> dict[str, float]:
    return {
        "minutes_since_event_start": float(state.minutes_since_event_start),
        "minutes_since_detection": float(state.minutes_since_detection),
        "event_alive": 1.0 if state.event_alive else 0.0,
        "time_since_running_high_minutes": float(state.time_since_running_high_minutes),
        "current_return_from_start": float(state.current_return_from_start),
        "distance_to_running_high": float(state.distance_to_running_high),
        "distance_to_running_low": float(state.distance_to_running_low),
        "distance_to_structural_low": 0.0 if state.distance_to_structural_low is None else float(state.distance_to_structural_low),
        "distance_to_structural_high": 0.0 if state.distance_to_structural_high is None else float(state.distance_to_structural_high),
        "missing_structural_low": 1.0 if state.distance_to_structural_low is None else 0.0,
        "missing_structural_high": 1.0 if state.distance_to_structural_high is None else 0.0,
    }


def _feature_matrix_value(row: AnomalyFeatureMatrixRow, field_name: str) -> float:
    value = getattr(row, field_name)
    if value is None:
        return math.nan
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raise PredictionInputError(f"feature matrix model feature must be numeric or bool, got {field_name!r}")


def _raw_probability_matrix(model: CatBoostClassifier, rows: Sequence[PredictionInputRow], *, feature_names: tuple[str, ...]) -> np.ndarray:
    return np.asarray(model.predict_proba(_feature_matrix(rows, feature_names=feature_names)), dtype=float)


def _probability_dict(values: Sequence[float] | np.ndarray) -> dict[str, float]:
    vector = _normalize_probability_array(np.asarray(values, dtype=float))
    return {scenario: float(vector[index]) for index, scenario in enumerate(PREDICTED_SCENARIOS)}


def _normalize_probability_array(values: np.ndarray) -> np.ndarray:
    raw = np.maximum(values.astype(float), 0.0)
    total = float(raw.sum())
    if total <= 0.0:
        return np.full(len(PREDICTED_SCENARIOS), 1.0 / len(PREDICTED_SCENARIOS), dtype=float)
    normalized = raw / total
    normalized[-1] = 1.0 - float(normalized[:-1].sum())
    return normalized


def _exact_model_key(bins: Mapping[str, str]) -> str:
    fields = ("maturity", "return", "distance_high", "distance_low", "time_since_high", "alive")
    return "exact|" + "|".join(f"{field}={bins[field]}" for field in fields)


def _partial_model_key(bins: Mapping[str, str]) -> str:
    fields = ("maturity", "return", "distance_high")
    return "partial|" + "|".join(f"{field}={bins[field]}" for field in fields)


def _smoothed_distribution(
    counts: Counter[str],
    *,
    prior: Mapping[str, float] | None,
    smoothing_strength: float,
) -> dict[str, float]:
    total = float(sum(counts.values()))
    if prior is None:
        if total <= 0:
            return {scenario: 1.0 / len(PREDICTED_SCENARIOS) for scenario in PREDICTED_SCENARIOS}
        distribution = {scenario: float(counts.get(scenario, 0)) / total for scenario in PREDICTED_SCENARIOS}
    else:
        denominator = total + smoothing_strength
        distribution = {
            scenario: (float(counts.get(scenario, 0)) + smoothing_strength * float(prior[scenario])) / denominator
            for scenario in PREDICTED_SCENARIOS
        }
    return _normalize_probabilities(distribution)


def _normalize_probabilities(distribution: Mapping[str, float]) -> dict[str, float]:
    raw = {scenario: max(0.0, float(distribution.get(scenario, 0.0))) for scenario in PREDICTED_SCENARIOS}
    total = sum(raw.values())
    if total <= 0:
        return {scenario: 1.0 / len(PREDICTED_SCENARIOS) for scenario in PREDICTED_SCENARIOS}
    normalized = {scenario: raw[scenario] / total for scenario in PREDICTED_SCENARIOS}
    # Make the dataclass sum check deterministic after CSV roundtrips.
    last = PREDICTED_SCENARIOS[-1]
    normalized[last] = 1.0 - sum(normalized[scenario] for scenario in PREDICTED_SCENARIOS[:-1])
    return normalized


def _predicted_scenario(probabilities: Mapping[str, float]) -> tuple[str, float]:
    scenario = max(PREDICTED_SCENARIOS, key=lambda name: (probabilities[name], -PREDICTED_SCENARIOS.index(name)))
    return scenario, probabilities[scenario]


def _brier_score(row: OosPredictionRow) -> float:
    return sum((_probability_for_scenario(row, scenario) - (1.0 if row.target_scenario == scenario else 0.0)) ** 2 for scenario in PREDICTED_SCENARIOS)


def _log_loss(row: OosPredictionRow) -> float:
    return -math.log(max(_probability_for_scenario(row, row.target_scenario), _EPS))


def _probability_for_scenario(row: OosPredictionRow, scenario: str) -> float:
    return {
        "long_continuation": row.p_long_continuation,
        "short_fade": row.p_short_fade,
        "static_or_chop": row.p_static_or_chop,
        "unclear": row.p_unclear,
    }[scenario]


def _probability_sum(row: OosPredictionRow) -> float:
    return sum(_probability_for_scenario(row, scenario) for scenario in PREDICTED_SCENARIOS)


def _confidence_bucket(confidence: float) -> str:
    lower = math.floor(confidence * 10.0) / 10.0
    upper = min(1.0, lower + 0.1)
    return f"[{lower:.1f},{upper:.1f})" if upper < 1.0 else "[0.9,1.0]"


def _expected_calibration_error(rows: Sequence[OosPredictionRow]) -> float:
    if not rows:
        return 0.0
    grouped: dict[str, list[OosPredictionRow]] = defaultdict(list)
    for row in rows:
        grouped[_confidence_bucket(row.prediction_confidence)].append(row)
    total = float(len(rows))
    return sum(
        (len(group) / total)
        * abs(
            _mean(item.prediction_confidence for item in group)
            - _mean(1.0 if item.predicted_scenario == item.target_scenario else 0.0 for item in group)
        )
        for group in grouped.values()
    )


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return float(sum(items)) / float(len(items))


def _count_trainless_test_rows(
    *,
    available_rows: Sequence[PredictionInputRow],
    test_weeks: Sequence[str],
    config: WalkForwardPredictionConfig,
) -> int:
    skipped = 0
    for test_week in test_weeks:
        weekly_model_freeze_time_ms = _week_start_ms(test_week)
        train_cutoff_time_ms = weekly_model_freeze_time_ms - config.purge_horizon_minutes * ONE_MINUTE_MS
        train_count = sum(1 for row in available_rows if row.state.snapshot_time_ms <= train_cutoff_time_ms)
        if train_count < config.min_train_rows:
            skipped += sum(1 for row in available_rows if _utc_week(row.state.snapshot_time_ms) == test_week)
    return skipped


def _integer_bucket(value: int, ranges: Sequence[tuple[int, int, str]], default: str) -> str:
    for lower, upper, label in ranges:
        if lower <= value <= upper:
            return label
    return default


def _float_bucket(value: float, upper_bounds: Sequence[tuple[float, str]], default: str) -> str:
    for upper_bound, label in upper_bounds:
        if value <= upper_bound:
            return label
    return default


def _utc_day(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _utc_week(timestamp_ms: int) -> str:
    parsed = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
    iso_year, iso_week, _ = parsed.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def _week_start_ms(week: str) -> int:
    parsed = datetime.strptime(f"{week}-1", "%G-W%V-%u").replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _metric_value(value: str | float | int) -> str:
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _required_str(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    result = str(value)
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _required_int(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    return int(value)


def _required_float(row: Mapping[str, object], name: str) -> float:
    value = row[name]
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    return float(value)
