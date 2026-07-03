from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly_science.probability import (
    BinaryProbabilityInputError,
    BinaryProbabilityGates,
    BinaryWeeklyWalkForwardConfig,
    build_binary_weekly_walk_forward,
    build_gate_rows,
    build_null_test_rows,
    build_prediction_metrics,
    build_reliability_rows,
    load_binary_weekly_walk_forward_config,
    run_binary_weekly_walk_forward,
    QUANTILE_BINNED_BETA_ISOTONIC_V2,
    PairedProbabilityComparisonConfig,
    PairedProbabilityError,
    compare_paired_oos_probabilities,
)
from anomaly_science.strategy.pump_fade.spec import PUMP_FADE_STRATEGY


def _ms(value: str) -> int:
    return int(pd.Timestamp(value, tz="UTC").timestamp() * 1000)


def _config() -> BinaryWeeklyWalkForwardConfig:
    return BinaryWeeklyWalkForwardConfig(
        protocol_freeze_id="binary-unit-v1",
        strategy_name="unit_strategy",
        target_name="future_fade",
        development_start_ms=_ms("2025-01-01"),
        oos_start_ms=_ms("2025-03-01"),
        oos_end_ms=_ms("2025-04-01"),
        numeric_features=("signal", "noise"),
        categorical_features=("session",),
        breakdown_columns=("session",),
        label_only_columns=("nature_y", "nature_resolution_time_ms"),
        required_label_schema_value="pump_fade_event_peak_close_race_v1",
        min_train_rows=80,
        min_split_rows=12,
        min_class_rows_per_split=3,
        catboost_iterations=35,
        catboost_depth=3,
        catboost_early_stopping_rounds=8,
        catboost_thread_count=1,
        null_permutations=19,
        gates=BinaryProbabilityGates(
            min_oos_rows=20,
            min_auc=0.5,
            min_log_loss_improvement=0.0,
            min_brier_improvement=0.0,
            max_ece=1.0,
            high_probability_threshold=0.7,
            min_high_probability_rows=1,
            min_high_probability_observed_rate=0.0,
            min_high_probability_wilson_lower_95=0.0,
            max_high_probability_calibration_gap=1.0,
        ),
    )


def _rows() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows: list[dict[str, object]] = []
    for day_index, day in enumerate(pd.date_range("2025-01-01", "2025-03-31", tz="UTC", freq="D")):
        for event_index in range(6):
            snapshot = day + pd.Timedelta(hours=event_index * 3)
            snapshot_ms = int(snapshot.timestamp() * 1000)
            target = int((day_index + event_index) % 2 == 0)
            rows.append(
                {
                    "group": f"g-{day_index}-{event_index}",
                    "symbol": f"S{event_index % 3}",
                    "snapshot_time_ms": snapshot_ms,
                    "feature_cutoff_time_ms": snapshot_ms,
                    "nature_future_start_time_ms": snapshot_ms + 60_000,
                    "nature_resolution_time_ms": snapshot_ms + 2 * 86_400_000,
                    "nature_y": target,
                    "nature_label_available": True,
                    "nature_label_schema_version": "pump_fade_event_peak_close_race_v1",
                    "is_nature_anchor": True,
                    "signal": target * 2.0 + rng.normal(0.0, 0.25),
                    "noise": rng.normal(),
                    "session": "asia" if event_index % 2 else "us",
                }
            )
    return pd.DataFrame(rows)


def test_weekly_binary_probability_is_frozen_and_causal() -> None:
    result = build_binary_weekly_walk_forward(_rows(), _config())

    assert len(result.predictions) > 100
    assert result.frozen_models
    assert set(result.weekly_metadata["status"]) == {"FROZEN_AND_SCORED"}
    assert (
        result.weekly_metadata["latest_train_resolution_time_ms"]
        < result.weekly_metadata["weekly_model_freeze_time_ms"]
    ).all()
    per_week_models = result.predictions.groupby("test_week")["model_id"].nunique()
    assert (per_week_models == 1).all()
    assert (
        result.predictions["weekly_model_freeze_time_ms"]
        <= result.predictions["snapshot_time_ms"]
    ).all()
    assert result.predictions["calibrated_probability"].between(0.0, 1.0).all()


def test_split_groups_isolate_recurrence_chains_across_week_boundaries() -> None:
    frame = _rows()
    day_block = (
        (frame["snapshot_time_ms"] - frame["snapshot_time_ms"].min())
        // (10 * 86_400_000)
    ).astype(str)
    frame["recurrence_chain_id"] = frame["symbol"] + ":chain:" + day_block
    config = replace(_config(), split_group_column="recurrence_chain_id")

    result = build_binary_weekly_walk_forward(frame, config)

    scored = result.weekly_metadata.loc[
        result.weekly_metadata["status"] == "FROZEN_AND_SCORED"
    ]
    assert not scored.empty
    assert (scored["excluded_train_same_split_group_row_count"] > 0).any()
    assert (
        scored["eligible_train_split_group_count"]
        < scored["eligible_train_group_count"]
    ).all()


def test_unresolved_labels_at_freeze_cannot_change_first_week_model() -> None:
    original = _rows()
    changed = original.copy()
    first_freeze = _ms("2025-02-24")
    unresolved = changed["nature_resolution_time_ms"] >= first_freeze
    changed.loc[unresolved, "nature_y"] = 1 - changed.loc[unresolved, "nature_y"]

    first = build_binary_weekly_walk_forward(original, _config()).predictions
    second = build_binary_weekly_walk_forward(changed, _config()).predictions
    first = first.loc[first["test_week"] == "2025-W09"]
    second = second.loc[second["test_week"] == "2025-W09"]

    assert first["group"].tolist() == second["group"].tolist()
    assert first["raw_probability"].to_numpy() == pytest.approx(
        second["raw_probability"].to_numpy()
    )


def test_metrics_reliability_and_gates_are_explicit() -> None:
    result = build_binary_weekly_walk_forward(_rows(), _config())
    predictions = result.predictions
    metrics = build_prediction_metrics(predictions, _config())
    reliability = build_reliability_rows(predictions, _config())
    null_tests = build_null_test_rows(predictions, _config())
    gates = build_gate_rows(
        predictions,
        metrics,
        reliability,
        null_tests,
        result.weekly_metadata,
        _config(),
    )

    overall = metrics.loc[(metrics["slice"] == "overall") & (metrics["slice_value"] == "all")]
    assert {"auc", "log_loss_improvement", "brier_improvement", "ece"} <= set(overall["metric"])
    assert {"overall", "week", "month", "symbol", "session"} <= set(metrics["slice"])
    assert set(reliability["kind"]) == {"fixed_bin", "threshold"}
    assert len(gates) == 12
    assert set(gates["status"]) <= {"PASS", "FAIL"}


def test_binary_probability_rejects_temporal_leakage() -> None:
    frame = _rows()
    frame.loc[0, "feature_cutoff_time_ms"] = frame.loc[0, "snapshot_time_ms"] + 1

    with pytest.raises(BinaryProbabilityInputError, match="feature_cutoff_time"):
        build_binary_weekly_walk_forward(frame, _config())


def test_binned_beta_isotonic_avoids_unsupported_zero_one_probabilities() -> None:
    config = replace(
        _config(),
        calibration_method=QUANTILE_BINNED_BETA_ISOTONIC_V2,
        isotonic_fit_bins=8,
        isotonic_beta_prior_strength=10.0,
    )

    predictions = build_binary_weekly_walk_forward(_rows(), config).predictions

    assert (predictions["calibrated_probability"] > 0.0).all()
    assert (predictions["calibrated_probability"] < 1.0).all()


def test_binary_probability_config_loader_is_strict(tmp_path: Path) -> None:
    payload = {
        "protocol_freeze_id": "loader-v1",
        "strategy_name": "unit",
        "target_name": "fade",
        "development_start_utc": "2025-01-01T00:00:00Z",
        "oos_start_utc": "2025-03-01T00:00:00Z",
        "oos_end_utc": "2025-04-01T00:00:00Z",
        "numeric_features": ["signal"],
        "required_label_schema_value": "unit_label_v1",
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_binary_weekly_walk_forward_config(path)

    assert loaded.numeric_features == ("signal",)
    assert loaded.oos_start_ms == _ms("2025-03-01")


def test_binary_probability_runner_writes_models_and_audits(tmp_path: Path) -> None:
    input_path = tmp_path / "nature.parquet"
    out_dir = tmp_path / "probability"
    _rows().to_parquet(input_path, index=False)

    result = run_binary_weekly_walk_forward(
        input_path=input_path,
        out_dir=out_dir,
        config=_config(),
        allow_dirty_development=True,
    )

    assert result == out_dir
    assert (out_dir / "oos_predictions.parquet").exists()
    assert (out_dir / "gate_evaluation.csv").exists()
    assert (out_dir / "temporal_audit.csv").exists()
    assert list((out_dir / "weekly_models").glob("*.cbm"))
    protocol = json.loads((out_dir / "frozen_probability_protocol.json").read_text(encoding="utf-8"))
    assert protocol["within_week_refit"] is False
    assert protocol["post_hoc_gate_changes_forbidden"] is True


def test_registered_pump_fade_probability_features_belong_to_strategy_catalog() -> None:
    config = load_binary_weekly_walk_forward_config(
        Path("research/pump_fade_t0_probability.json")
    )
    model_catalog = {
        feature.name
        for feature in PUMP_FADE_STRATEGY.custom_feature_catalog
        if feature.is_model_feature
    }

    assert set(config.numeric_features) <= model_catalog
    assert set(config.categorical_features) <= model_catalog
    assert config.population_minimum_column in model_catalog


def test_registered_pump_fade_state_features_belong_to_strategy_catalog() -> None:
    config = load_binary_weekly_walk_forward_config(
        Path("research/pump_fade_state_probability.json")
    )
    model_catalog = {
        feature.name
        for feature in PUMP_FADE_STRATEGY.custom_feature_catalog
        if feature.is_model_feature
    }

    assert set(config.numeric_features) <= model_catalog
    assert set(config.categorical_features) <= model_catalog
    assert config.population_exact_column == "state_ordinal"
    assert config.calibration_method == QUANTILE_BINNED_BETA_ISOTONIC_V2


def test_paired_probability_comparison_uses_identical_oos_population() -> None:
    rows = []
    for week in range(20):
        for index in range(20):
            target = (week + index) % 2
            rows.append(
                {
                    "group": f"g-{week}-{index}",
                    "symbol": f"S{index % 4}",
                    "snapshot_time_ms": week * 1_000_000 + index,
                    "test_week": f"2025-W{week + 1:02d}",
                    "target": target,
                    "weekly_model_freeze_time_ms": week * 1_000_000,
                    "calibrated_probability": 0.45 if target else 0.55,
                }
            )
    baseline = pd.DataFrame(rows)
    augmented = baseline.copy()
    augmented["calibrated_probability"] = np.where(augmented["target"] == 1, 0.8, 0.2)
    config = PairedProbabilityComparisonConfig(
        protocol_freeze_id="paired-unit-v1",
        bootstrap_iterations=1_000,
        sign_flip_iterations=999,
    )

    result = compare_paired_oos_probabilities(baseline, augmented, config)

    observed = dict(zip(result.inference["metric"], result.inference["observed_delta"]))
    assert observed["auc_delta"] > 0.9
    assert observed["log_loss_improvement"] > 0.0
    assert observed["brier_improvement"] > 0.0
    assert (result.gates["status"] == "PASS").all()

    with pytest.raises(PairedProbabilityError, match="populations differ"):
        compare_paired_oos_probabilities(baseline.iloc[:-1], augmented, config)
