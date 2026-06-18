from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping, Sequence

from anomaly_science.contracts.controls import (
    CONTROL_STATUS_DEFERRED,
    CONTROL_STATUS_OK,
    CONTROL_STATUS_SKIPPED,
    BaselineComparisonRow,
    PlaceboTestRow,
)
from anomaly_science.contracts.labels import AnomalyOutcomeLabelRow, MISSING_FUTURE_SCENARIO
from anomaly_science.contracts.prediction import OosPredictionRow, PREDICTED_SCENARIOS
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.controls.config import ControlsConfig
from anomaly_science.prediction.builder import PredictionInputRow, build_walk_forward_predictions
from anomaly_science.prediction.config import WalkForwardPredictionConfig

ONE_MINUTE_MS = 60_000
_EPS = 1e-15


class ControlsArtifactError(ValueError):
    """Raised when MVP1 control artifacts cannot be built cleanly."""


@dataclass(frozen=True, slots=True)
class ControlEvaluation:
    available_label_rows: int
    oos_prediction_rows: int
    accuracy: float
    multiclass_brier: float
    log_loss: float


def build_placebo_test_rows(
    *,
    inputs: Sequence[PredictionInputRow] | Iterable[PredictionInputRow],
    config: ControlsConfig | None = None,
) -> tuple[PlaceboTestRow, ...]:
    """Build negative placebo-control summaries for the Patch 9 prediction layer."""
    cfg = config or ControlsConfig()
    rows = _available_rows(inputs, cfg)
    reference = _evaluate_reference_model(rows=rows, config=cfg)
    reference_brier = reference.multiclass_brier
    if len(rows) < 2:
        return tuple(
            _placebo_row(
                cfg=cfg,
                control_name=name,
                evaluation=ControlEvaluation(len(rows), 0, 0.0, 0.0, 0.0),
                reference_brier=reference_brier,
                status=CONTROL_STATUS_SKIPPED,
                notes="not enough non-missing labels for placebo shuffling",
            )
            for name in ("random_labels", "time_shuffled_labels", "symbol_shuffled_labels", "random_entry_times")
        )

    placebo_maps: list[tuple[str, dict[tuple[str, str, int, int], str], str]] = [
        ("random_labels", _random_label_map(rows=rows, cfg=cfg), "deterministic random permutation of descriptive scenario targets"),
        ("time_shuffled_labels", _time_shuffled_label_map(rows=rows, cfg=cfg), "deterministic circular time shift of descriptive scenario targets"),
    ]
    symbol_map = _symbol_shuffled_label_map(rows=rows, cfg=cfg)
    if symbol_map is None:
        symbol_status = CONTROL_STATUS_SKIPPED
        symbol_notes = "symbol-shuffled placebo requires at least two symbols with non-missing labels"
        symbol_evaluation = ControlEvaluation(len(rows), 0, 0.0, 0.0, 0.0)
    else:
        symbol_status = CONTROL_STATUS_OK
        symbol_notes = "deterministic cross-symbol rotation of descriptive scenario targets"
        symbol_evaluation = _evaluate_reference_model(rows=_replace_targets(rows=rows, target_by_key=symbol_map, cfg=cfg), config=cfg)

    result: list[PlaceboTestRow] = []
    for control_name, target_by_key, notes in placebo_maps:
        evaluation = _evaluate_reference_model(rows=_replace_targets(rows=rows, target_by_key=target_by_key, cfg=cfg), config=cfg)
        result.append(
            _placebo_row(
                cfg=cfg,
                control_name=control_name,
                evaluation=evaluation,
                reference_brier=reference_brier,
                status=CONTROL_STATUS_OK if evaluation.oos_prediction_rows > 0 else CONTROL_STATUS_SKIPPED,
                notes=notes if evaluation.oos_prediction_rows > 0 else "placebo produced no OOS rows after train-size and purge checks",
            )
        )
    result.append(
        _placebo_row(
            cfg=cfg,
            control_name="symbol_shuffled_labels",
            evaluation=symbol_evaluation,
            reference_brier=reference_brier,
            status=symbol_status if symbol_evaluation.oos_prediction_rows > 0 else CONTROL_STATUS_SKIPPED,
            notes=symbol_notes if symbol_evaluation.oos_prediction_rows > 0 else symbol_notes,
        )
    )
    result.append(
        _placebo_row(
            cfg=cfg,
            control_name="random_entry_times",
            evaluation=ControlEvaluation(len(rows), 0, 0.0, 0.0, 0.0),
            reference_brier=reference_brier,
            status=CONTROL_STATUS_DEFERRED,
            notes="deferred cleanly: random entry-time controls belong to trade simulation and require simulated entry artifacts",
        )
    )
    return tuple(result)


def build_baseline_comparison_rows(
    *,
    inputs: Sequence[PredictionInputRow] | Iterable[PredictionInputRow],
    config: ControlsConfig | None = None,
) -> tuple[BaselineComparisonRow, ...]:
    """Build simple baseline comparisons without trading thresholds or PnL."""
    cfg = config or ControlsConfig()
    rows = _available_rows(inputs, cfg)
    reference = _evaluate_reference_model(rows=rows, config=cfg)
    reference_brier = reference.multiclass_brier
    target_by_key = {_row_key(row): _target_for_horizon(row.label, cfg.target_horizon_minutes) for row in rows}

    has_feature_matrix = any(row.features is not None for row in rows)
    baseline_specs: tuple[tuple[str, str, Callable[[PredictionInputRow], str], str], ...] = (
        ("global_prior_only", "global_label_prior", _global_feature_key, "weekly frozen-model global class-prior baseline"),
        ("session_only", "snapshot_utc_session", _session_feature_key, "weekly frozen-model baseline using only UTC session/time buckets"),
        ("event_time_only", "event_clock", _event_time_feature_key, "weekly frozen-model baseline using only minutes since detection/event start"),
        ("price_path_only", "state_price_path", _price_path_feature_key, "weekly frozen-model baseline using only online price-path state bins"),
    )
    result: list[BaselineComparisonRow] = []
    for baseline_name, feature_family, feature_key_fn, notes in baseline_specs:
        evaluation = _evaluate_feature_family_model(
            rows=rows,
            target_by_key=target_by_key,
            feature_key_fn=feature_key_fn,
            config=cfg,
        )
        result.append(
            _baseline_row(
                cfg=cfg,
                baseline_name=baseline_name,
                feature_family=feature_family,
                evaluation=evaluation,
                reference_brier=reference_brier,
                status=CONTROL_STATUS_OK if evaluation.oos_prediction_rows > 0 else CONTROL_STATUS_SKIPPED,
                notes=notes if evaluation.oos_prediction_rows > 0 else "baseline produced no OOS rows after train-size and purge checks",
            )
        )

    if has_feature_matrix:
        feature_matrix_specs: tuple[tuple[str, str, Callable[[PredictionInputRow], str], str], ...] = (
            ("volume_only", "volume", _volume_feature_key, "weekly frozen-model baseline using only volume-relative feature bins"),
            ("btc_eth_only", "market_anchor", _btc_relative_feature_key, "weekly frozen-model baseline using only BTC-relative context bins"),
        )
        for baseline_name, feature_family, feature_key_fn, notes in feature_matrix_specs:
            evaluation = _evaluate_feature_family_model(
                rows=rows,
                target_by_key=target_by_key,
                feature_key_fn=feature_key_fn,
                config=cfg,
            )
            result.append(
                _baseline_row(
                    cfg=cfg,
                    baseline_name=baseline_name,
                    feature_family=feature_family,
                    evaluation=evaluation,
                    reference_brier=reference_brier,
                    status=CONTROL_STATUS_OK if evaluation.oos_prediction_rows > 0 else CONTROL_STATUS_SKIPPED,
                    notes=notes if evaluation.oos_prediction_rows > 0 else "feature-matrix baseline produced no OOS rows after train-size and purge checks",
                )
            )

    rule_specs: tuple[tuple[str, str, Callable[[AnomalyState1mRow], str], str], ...] = (
        ("always_follow_anomaly", "anomaly_direction_rule", _always_follow_anomaly, "anomaly-specific rule baseline: always predict follow-through"),
        ("always_fade_anomaly", "anomaly_direction_rule", _always_fade_anomaly, "anomaly-specific rule baseline: always predict fade"),
        ("fade_only_after_extension", "anomaly_extension_rule", _fade_only_after_extension, "anomaly-specific rule baseline: fade only after online extension"),
        ("follow_only_early_squeeze", "anomaly_early_squeeze_rule", _follow_only_early_squeeze, "anomaly-specific rule baseline: follow only early online squeeze state"),
    )
    for baseline_name, feature_family, rule_fn, notes in rule_specs:
        evaluation = _evaluate_static_rule_model(rows=rows, target_by_key=target_by_key, rule_fn=rule_fn)
        result.append(
            _baseline_row(
                cfg=cfg,
                baseline_name=baseline_name,
                feature_family=feature_family,
                evaluation=evaluation,
                reference_brier=reference_brier,
                status=CONTROL_STATUS_OK if evaluation.oos_prediction_rows > 0 else CONTROL_STATUS_SKIPPED,
                notes=notes if evaluation.oos_prediction_rows > 0 else "rule baseline produced no rows with available labels",
            )
        )

    if has_feature_matrix:
        ablation_specs = (
            ("no_cvd_features_ablation", "flow_ablation", ("feature_matrix.cvd_", "feature_matrix.price_up_cvd", "feature_matrix.price_down_cvd"), "CatBoost ablation excluding CVD divergence feature prefixes"),
            ("no_oi_features_ablation", "open_interest_ablation", ("feature_matrix.oi_", "feature_matrix.closed_5m_oi"), "CatBoost ablation excluding open-interest feature prefixes"),
            ("no_liquidation_features_ablation", "liquidation_ablation", ("feature_matrix.short_liq", "feature_matrix.long_liq", "feature_matrix.liquidation_", "feature_matrix.cumulative_liq", "feature_matrix.liq_intensity"), "CatBoost ablation excluding liquidation feature prefixes"),
        )
        for baseline_name, feature_family, excluded_prefixes, notes in ablation_specs:
            evaluation = _evaluate_reference_model_with_exclusions(rows=rows, config=cfg, excluded_prefixes=excluded_prefixes)
            result.append(
                _baseline_row(
                    cfg=cfg,
                    baseline_name=baseline_name,
                    feature_family=feature_family,
                    evaluation=evaluation,
                    reference_brier=reference_brier,
                    status=CONTROL_STATUS_OK if evaluation.oos_prediction_rows > 0 else CONTROL_STATUS_SKIPPED,
                    notes=notes if evaluation.oos_prediction_rows > 0 else "ablation produced no OOS rows after train-size, class-coverage, and purge checks",
                )
            )
        subset_specs = (
            ("idiosyncratic_only_subset", "market_shock_subset", "idiosyncratic", "CatBoost evaluated only on idiosyncratic anomaly subset"),
            ("systemic_cluster_only_subset", "market_shock_subset", "systemic_beta_shock", "CatBoost evaluated only on systemic beta shock anomaly subset"),
        )
        for baseline_name, feature_family, regime, notes in subset_specs:
            subset_rows = tuple(row for row in rows if row.features is not None and row.features.systemic_cluster_regime == regime)
            evaluation = _evaluate_reference_model(rows=subset_rows, config=cfg)
            result.append(
                _baseline_row(
                    cfg=cfg,
                    baseline_name=baseline_name,
                    feature_family=feature_family,
                    evaluation=evaluation,
                    reference_brier=reference_brier,
                    status=CONTROL_STATUS_OK if evaluation.oos_prediction_rows > 0 else CONTROL_STATUS_SKIPPED,
                    notes=notes if evaluation.oos_prediction_rows > 0 else f"subset {regime} produced no OOS rows after train-size, class-coverage, and purge checks",
                )
            )

    deferred_specs = (
        *(() if has_feature_matrix else (
            ("volume_only", "volume", "deferred cleanly: anomaly_feature_matrix.csv was not supplied, so no proxy volume baseline is emitted"),
            ("btc_eth_only", "market_anchor", "deferred cleanly: anomaly_feature_matrix.csv was not supplied, so no proxy market-anchor baseline is emitted"),
            ("no_cvd_features_ablation", "flow_ablation", "deferred cleanly: anomaly_feature_matrix.csv was not supplied, so no proxy CVD ablation is emitted"),
            ("no_oi_features_ablation", "open_interest_ablation", "deferred cleanly: anomaly_feature_matrix.csv was not supplied, so no proxy OI ablation is emitted"),
            ("no_liquidation_features_ablation", "liquidation_ablation", "deferred cleanly: anomaly_feature_matrix.csv was not supplied, so no proxy liquidation ablation is emitted"),
            ("idiosyncratic_only_subset", "market_shock_subset", "deferred cleanly: anomaly_feature_matrix.csv was not supplied, so no proxy idiosyncratic subset is emitted"),
            ("systemic_cluster_only_subset", "market_shock_subset", "deferred cleanly: anomaly_feature_matrix.csv was not supplied, so no proxy systemic subset is emitted"),
        )),
        ("always_no_trade", "decision_baseline", "deferred cleanly: always no-trade baseline belongs to decision/simulation artifacts, not scenario prediction probabilities"),
        ("strategy_specific_heuristic", "strategy_heuristic", "deferred cleanly: no pre-registered strategy-specific heuristic baseline exists for MVP1 controls"),
    )
    for baseline_name, feature_family, notes in deferred_specs:
        result.append(
            _baseline_row(
                cfg=cfg,
                baseline_name=baseline_name,
                feature_family=feature_family,
                evaluation=ControlEvaluation(available_label_rows=len(rows), oos_prediction_rows=0, accuracy=0.0, multiclass_brier=0.0, log_loss=0.0),
                reference_brier=reference_brier,
                status=CONTROL_STATUS_DEFERRED,
                notes=notes,
            )
        )
    return tuple(result)


def placebo_test_rows_to_artifact(rows: Sequence[PlaceboTestRow] | Iterable[PlaceboTestRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def baseline_comparison_rows_to_artifact(rows: Sequence[BaselineComparisonRow] | Iterable[BaselineComparisonRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def _available_rows(inputs: Sequence[PredictionInputRow] | Iterable[PredictionInputRow], cfg: ControlsConfig) -> tuple[PredictionInputRow, ...]:
    rows = tuple(inputs)
    available = [row for row in rows if _target_for_horizon(row.label, cfg.target_horizon_minutes) != MISSING_FUTURE_SCENARIO]
    available.sort(key=lambda row: (row.state.snapshot_time_ms, row.state.symbol, row.state.event_id))
    return tuple(available)


def _evaluate_reference_model(*, rows: Sequence[PredictionInputRow], config: ControlsConfig) -> ControlEvaluation:
    prediction_config = WalkForwardPredictionConfig(
        target_horizon_minutes=config.target_horizon_minutes,
        purge_horizon_minutes=config.purge_horizon_minutes,
        min_train_rows=config.min_train_rows,
        min_group_rows=config.min_group_rows,
        smoothing_strength=config.smoothing_strength,
    )
    predictions = build_walk_forward_predictions(inputs=rows, config=prediction_config)
    return _evaluate_oos_predictions(available_label_rows=len(rows), predictions=predictions)


def _evaluate_reference_model_with_exclusions(
    *,
    rows: Sequence[PredictionInputRow],
    config: ControlsConfig,
    excluded_prefixes: Sequence[str],
) -> ControlEvaluation:
    prediction_config = WalkForwardPredictionConfig(
        target_horizon_minutes=config.target_horizon_minutes,
        purge_horizon_minutes=config.purge_horizon_minutes,
        min_train_rows=config.min_train_rows,
        min_group_rows=config.min_group_rows,
        smoothing_strength=config.smoothing_strength,
        excluded_model_feature_prefixes=tuple(excluded_prefixes),
    )
    predictions = build_walk_forward_predictions(inputs=rows, config=prediction_config)
    return _evaluate_oos_predictions(available_label_rows=len(rows), predictions=predictions)


def _evaluate_oos_predictions(*, available_label_rows: int, predictions: Sequence[OosPredictionRow]) -> ControlEvaluation:
    if not predictions:
        return ControlEvaluation(
            available_label_rows=available_label_rows,
            oos_prediction_rows=0,
            accuracy=0.0,
            multiclass_brier=0.0,
            log_loss=0.0,
        )
    return ControlEvaluation(
        available_label_rows=available_label_rows,
        oos_prediction_rows=len(predictions),
        accuracy=_mean(1.0 if row.predicted_scenario == row.target_scenario else 0.0 for row in predictions),
        multiclass_brier=_mean(_prediction_brier(row) for row in predictions),
        log_loss=_mean(_prediction_log_loss(row) for row in predictions),
    )


def _evaluate_feature_family_model(
    *,
    rows: Sequence[PredictionInputRow],
    target_by_key: Mapping[tuple[str, str, int, int], str],
    feature_key_fn: Callable[[PredictionInputRow], str],
    config: ControlsConfig,
) -> ControlEvaluation:
    if not rows:
        return ControlEvaluation(available_label_rows=0, oos_prediction_rows=0, accuracy=0.0, multiclass_brier=0.0, log_loss=0.0)

    rows_by_test_week: dict[str, list[PredictionInputRow]] = defaultdict(list)
    for row in rows:
        rows_by_test_week[_utc_week(row.state.snapshot_time_ms)].append(row)

    evaluated: list[tuple[str, dict[str, float], str]] = []
    for test_week in sorted(rows_by_test_week):
        weekly_model_freeze_time_ms = _week_start_ms(test_week)
        train_cutoff_time_ms = weekly_model_freeze_time_ms - config.purge_horizon_minutes * ONE_MINUTE_MS
        train_rows = [row for row in rows if row.state.snapshot_time_ms <= train_cutoff_time_ms]
        if len(train_rows) < config.min_train_rows:
            continue
        model = _FeatureFamilyEmpiricalModel.fit(
            rows=train_rows,
            target_by_key=target_by_key,
            feature_key_fn=feature_key_fn,
            config=config,
        )
        for test_row in sorted(rows_by_test_week[test_week], key=lambda row: (row.state.snapshot_time_ms, row.state.symbol, row.state.event_id)):
            target = target_by_key[_row_key(test_row)]
            probabilities = model.predict(test_row)
            predicted = _predicted_scenario(probabilities)
            evaluated.append((target, probabilities, predicted))

    if not evaluated:
        return ControlEvaluation(available_label_rows=len(rows), oos_prediction_rows=0, accuracy=0.0, multiclass_brier=0.0, log_loss=0.0)
    return ControlEvaluation(
        available_label_rows=len(rows),
        oos_prediction_rows=len(evaluated),
        accuracy=_mean(1.0 if predicted == target else 0.0 for target, _, predicted in evaluated),
        multiclass_brier=_mean(_brier_for_distribution(target=target, probabilities=probabilities) for target, probabilities, _ in evaluated),
        log_loss=_mean(-math.log(max(probabilities[target], _EPS)) for target, probabilities, _ in evaluated),
    )


def _evaluate_static_rule_model(
    *,
    rows: Sequence[PredictionInputRow],
    target_by_key: Mapping[tuple[str, str, int, int], str],
    rule_fn: Callable[[AnomalyState1mRow], str],
) -> ControlEvaluation:
    evaluated: list[tuple[str, dict[str, float], str]] = []
    for row in rows:
        target = target_by_key[_row_key(row)]
        predicted = rule_fn(row.state)
        probabilities = {scenario: 0.0 for scenario in PREDICTED_SCENARIOS}
        probabilities[predicted] = 1.0
        evaluated.append((target, probabilities, predicted))
    if not evaluated:
        return ControlEvaluation(available_label_rows=0, oos_prediction_rows=0, accuracy=0.0, multiclass_brier=0.0, log_loss=0.0)
    return ControlEvaluation(
        available_label_rows=len(rows),
        oos_prediction_rows=len(evaluated),
        accuracy=_mean(1.0 if predicted == target else 0.0 for target, _, predicted in evaluated),
        multiclass_brier=_mean(_brier_for_distribution(target=target, probabilities=probabilities) for target, probabilities, _ in evaluated),
        log_loss=_mean(-math.log(max(probabilities[target], _EPS)) for target, probabilities, _ in evaluated),
    )


class _FeatureFamilyEmpiricalModel:
    def __init__(
        self,
        *,
        group_counts: Mapping[str, Counter[str]],
        global_counts: Counter[str],
        feature_key_fn: Callable[[PredictionInputRow], str],
        config: ControlsConfig,
    ) -> None:
        self._group_counts = dict(group_counts)
        self._global_counts = global_counts
        self._global_prior = _smoothed_distribution(global_counts, prior=None, smoothing_strength=0.0)
        self._feature_key_fn = feature_key_fn
        self._config = config

    @classmethod
    def fit(
        cls,
        *,
        rows: Sequence[PredictionInputRow],
        target_by_key: Mapping[tuple[str, str, int, int], str],
        feature_key_fn: Callable[[PredictionInputRow], str],
        config: ControlsConfig,
    ) -> _FeatureFamilyEmpiricalModel:
        group_counts: dict[str, Counter[str]] = defaultdict(Counter)
        global_counts: Counter[str] = Counter()
        for row in rows:
            target = target_by_key[_row_key(row)]
            group_counts[feature_key_fn(row)][target] += 1
            global_counts[target] += 1
        return cls(group_counts=group_counts, global_counts=global_counts, feature_key_fn=feature_key_fn, config=config)

    def predict(self, row: PredictionInputRow) -> dict[str, float]:
        key = self._feature_key_fn(row)
        group = self._group_counts.get(key)
        if group is not None and sum(group.values()) >= self._config.min_group_rows:
            return _smoothed_distribution(group, prior=self._global_prior, smoothing_strength=self._config.smoothing_strength)
        return dict(self._global_prior)


def _placebo_row(
    *,
    cfg: ControlsConfig,
    control_name: str,
    evaluation: ControlEvaluation,
    reference_brier: float,
    status: str,
    notes: str,
) -> PlaceboTestRow:
    return PlaceboTestRow(
        control_version=cfg.control_version,
        control_name=control_name,
        target_horizon_minutes=cfg.target_horizon_minutes,
        random_seed=cfg.random_seed,
        available_label_rows=evaluation.available_label_rows,
        oos_prediction_rows=evaluation.oos_prediction_rows,
        accuracy=evaluation.accuracy,
        multiclass_brier=evaluation.multiclass_brier,
        log_loss=evaluation.log_loss,
        reference_real_brier=reference_brier,
        brier_delta_vs_real=evaluation.multiclass_brier - reference_brier,
        status=status,
        notes=notes,
    )


def _baseline_row(
    *,
    cfg: ControlsConfig,
    baseline_name: str,
    feature_family: str,
    evaluation: ControlEvaluation,
    reference_brier: float,
    status: str,
    notes: str,
) -> BaselineComparisonRow:
    return BaselineComparisonRow(
        control_version=cfg.control_version,
        baseline_name=baseline_name,
        feature_family=feature_family,
        target_horizon_minutes=cfg.target_horizon_minutes,
        available_label_rows=evaluation.available_label_rows,
        oos_prediction_rows=evaluation.oos_prediction_rows,
        accuracy=evaluation.accuracy,
        multiclass_brier=evaluation.multiclass_brier,
        log_loss=evaluation.log_loss,
        reference_real_brier=reference_brier,
        brier_delta_vs_real=evaluation.multiclass_brier - reference_brier,
        status=status,
        notes=notes,
    )


def _replace_targets(
    *,
    rows: Sequence[PredictionInputRow],
    target_by_key: Mapping[tuple[str, str, int, int], str],
    cfg: ControlsConfig,
) -> tuple[PredictionInputRow, ...]:
    result: list[PredictionInputRow] = []
    for row in rows:
        target = target_by_key[_row_key(row)]
        label = _label_with_target(label=row.label, horizon_minutes=cfg.target_horizon_minutes, target=target)
        result.append(PredictionInputRow(state=row.state, label=label, features=row.features))
    return tuple(result)


def _label_with_target(*, label: AnomalyOutcomeLabelRow, horizon_minutes: int, target: str) -> AnomalyOutcomeLabelRow:
    if target not in PREDICTED_SCENARIOS:
        raise ControlsArtifactError(f"placebo target must be predictable in MVP1, got {target!r}")
    if horizon_minutes == 15:
        return replace(label, scenario_15m=target, label_available_15m=True)
    if horizon_minutes == 30:
        return replace(label, scenario_30m=target, label_available_30m=True)
    if horizon_minutes == 60:
        return replace(label, scenario_60m=target, label_available_60m=True)
    if horizon_minutes == 120:
        return replace(label, scenario_120m=target, label_available_120m=True)
    raise ControlsArtifactError(f"unsupported target horizon: {horizon_minutes}")


def _random_label_map(*, rows: Sequence[PredictionInputRow], cfg: ControlsConfig) -> dict[tuple[str, str, int, int], str]:
    keys = [_row_key(row) for row in rows]
    targets = [_target_for_horizon(row.label, cfg.target_horizon_minutes) for row in rows]
    rng = random.Random(cfg.random_seed)
    shuffled = list(targets)
    rng.shuffle(shuffled)
    return dict(zip(keys, shuffled, strict=True))


def _time_shuffled_label_map(*, rows: Sequence[PredictionInputRow], cfg: ControlsConfig) -> dict[tuple[str, str, int, int], str]:
    if not rows:
        return {}
    # Use a deterministic circular shift large enough to break row-level alignment
    # without sampling labels from outside the available-label population.
    shift = max(1, len(rows) // 3)
    targets = [_target_for_horizon(row.label, cfg.target_horizon_minutes) for row in rows]
    shifted = targets[-shift:] + targets[:-shift]
    return {_row_key(row): target for row, target in zip(rows, shifted, strict=True)}


def _symbol_shuffled_label_map(*, rows: Sequence[PredictionInputRow], cfg: ControlsConfig) -> dict[tuple[str, str, int, int], str] | None:
    by_symbol: dict[str, list[PredictionInputRow]] = defaultdict(list)
    for row in rows:
        by_symbol[row.state.symbol].append(row)
    symbols = sorted(by_symbol)
    if len(symbols) < 2:
        return None
    result: dict[tuple[str, str, int, int], str] = {}
    for target_index, symbol in enumerate(symbols):
        source_symbol = symbols[target_index - 1]
        source_rows = sorted(by_symbol[source_symbol], key=lambda row: (row.state.snapshot_time_ms, row.state.event_id))
        target_rows = sorted(by_symbol[symbol], key=lambda row: (row.state.snapshot_time_ms, row.state.event_id))
        source_targets = [_target_for_horizon(row.label, cfg.target_horizon_minutes) for row in source_rows]
        for index, target_row in enumerate(target_rows):
            result[_row_key(target_row)] = source_targets[index % len(source_targets)]
    return result


def _target_for_horizon(label: AnomalyOutcomeLabelRow, horizon_minutes: int) -> str:
    if horizon_minutes == 15:
        return label.scenario_15m
    if horizon_minutes == 30:
        return label.scenario_30m
    if horizon_minutes == 60:
        return label.scenario_60m
    if horizon_minutes == 120:
        return label.scenario_120m
    raise ControlsArtifactError(f"unsupported target horizon: {horizon_minutes}")


def _row_key(row: PredictionInputRow) -> tuple[str, str, int, int]:
    return (row.state.event_id, row.state.symbol, row.state.snapshot_time_ms, row.state.feature_cutoff_time_ms)


def _global_feature_key(row: PredictionInputRow) -> str:
    return "global_prior_only"


def _session_feature_key(row: PredictionInputRow) -> str:
    state = row.state
    moment = datetime.fromtimestamp(state.snapshot_time_ms / 1000.0, tz=timezone.utc)
    hour_bucket = _integer_bucket(moment.hour, ((0, 5, "utc_00_05"), (6, 11, "utc_06_11"), (12, 17, "utc_12_17")), "utc_18_23")
    weekday = "weekday" if moment.weekday() < 5 else "weekend"
    return f"session|hour={hour_bucket}|day={weekday}"


def _event_time_feature_key(row: PredictionInputRow) -> str:
    state = row.state
    detection = _integer_bucket(state.minutes_since_detection, ((0, 2, "detect_0_2m"), (3, 5, "detect_3_5m"), (6, 10, "detect_6_10m")), "detect_11m_plus")
    event_age = _integer_bucket(state.minutes_since_event_start, ((0, 5, "age_0_5m"), (6, 15, "age_6_15m"), (16, 30, "age_16_30m")), "age_31m_plus")
    return f"event_time|{detection}|{event_age}"


def _price_path_feature_key(row: PredictionInputRow) -> str:
    state = row.state
    return_bucket = _float_bucket(state.current_return_from_start, ((-0.02, "return_deep_negative"), (-0.005, "return_negative"), (0.005, "return_flat"), (0.02, "return_positive")), "return_strong_positive")
    high_bucket = _float_bucket(state.distance_to_running_high, ((-0.03, "far_below_high"), (-0.01, "below_high"), (-0.001, "near_high"), (0.001, "at_high")), "above_high")
    low_bucket = _float_bucket(state.distance_to_running_low, ((0.001, "at_low"), (0.01, "near_low"), (0.03, "above_low")), "far_above_low")
    high_age = _integer_bucket(state.time_since_running_high_minutes, ((0, 0, "just_made_high"), (1, 3, "high_1_3m_ago"), (4, 10, "high_4_10m_ago")), "high_11m_plus_ago")
    return f"price_path|{return_bucket}|{high_bucket}|{low_bucket}|{high_age}"


def _volume_feature_key(row: PredictionInputRow) -> str:
    if row.features is None:
        return "volume|missing_feature_matrix"
    volume = _nullable_float_bucket(row.features.volume_zscore, ((-1.0, "vol_low"), (1.0, "vol_mid"), (3.0, "vol_high")), "vol_extreme")
    quote = _nullable_float_bucket(row.features.quote_volume_market_percentile, ((0.25, "qvol_p25"), (0.50, "qvol_p50"), (0.75, "qvol_p75")), "qvol_p100")
    return f"volume|{volume}|{quote}"


def _btc_relative_feature_key(row: PredictionInputRow) -> str:
    if row.features is None:
        return "btc_relative|missing_feature_matrix"
    corr = _nullable_float_bucket(row.features.corr_with_btc_30m, ((-0.25, "corr_negative"), (0.25, "corr_low"), (0.60, "corr_mid")), "corr_high")
    rel_return = _nullable_float_bucket(row.features.symbol_return_minus_btc_return_15m, ((-0.01, "underperform"), (0.01, "inline"), (0.03, "outperform")), "strong_outperform")
    return f"btc_relative|{corr}|{rel_return}"


def _always_follow_anomaly(state: AnomalyState1mRow) -> str:
    return "long_continuation"


def _always_fade_anomaly(state: AnomalyState1mRow) -> str:
    return "short_fade"


def _fade_only_after_extension(state: AnomalyState1mRow) -> str:
    if state.current_return_from_start >= 0.02 or state.distance_to_running_high >= -0.001:
        return "short_fade"
    return "static_or_chop"


def _follow_only_early_squeeze(state: AnomalyState1mRow) -> str:
    if state.minutes_since_detection <= 3 and state.distance_to_running_high >= -0.001:
        return "long_continuation"
    return "static_or_chop"


def _smoothed_distribution(counts: Counter[str], *, prior: Mapping[str, float] | None, smoothing_strength: float) -> dict[str, float]:
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
    last = PREDICTED_SCENARIOS[-1]
    normalized[last] = 1.0 - sum(normalized[scenario] for scenario in PREDICTED_SCENARIOS[:-1])
    return normalized


def _predicted_scenario(probabilities: Mapping[str, float]) -> str:
    return max(PREDICTED_SCENARIOS, key=lambda name: (probabilities[name], -PREDICTED_SCENARIOS.index(name)))


def _prediction_brier(row: OosPredictionRow) -> float:
    return _brier_for_distribution(target=row.target_scenario, probabilities=_probabilities_from_prediction(row))


def _prediction_log_loss(row: OosPredictionRow) -> float:
    return -math.log(max(_probabilities_from_prediction(row)[row.target_scenario], _EPS))


def _probabilities_from_prediction(row: OosPredictionRow) -> dict[str, float]:
    return {
        "long_continuation": row.p_long_continuation,
        "short_fade": row.p_short_fade,
        "static_or_chop": row.p_static_or_chop,
        "unclear": row.p_unclear,
    }


def _brier_for_distribution(*, target: str, probabilities: Mapping[str, float]) -> float:
    return sum((probabilities[scenario] - (1.0 if target == scenario else 0.0)) ** 2 for scenario in PREDICTED_SCENARIOS)


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return float(sum(items)) / float(len(items))


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


def _nullable_float_bucket(value: float | None, upper_bounds: Sequence[tuple[float, str]], default: str) -> str:
    if value is None:
        return "missing"
    return _float_bucket(value, upper_bounds, default)


def _utc_day(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _utc_week(timestamp_ms: int) -> str:
    parsed = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
    iso_year, iso_week, _ = parsed.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def _week_start_ms(week: str) -> int:
    return int(datetime.strptime(f"{week}-1", "%G-W%V-%u").replace(tzinfo=timezone.utc).timestamp() * 1000)
