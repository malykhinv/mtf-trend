"""Paired internal-IS weekly walk-forward ablation for aggTrades features."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.probability import (
    QUANTILE_BINNED_BETA_ISOTONIC_V2,
    BinaryProbabilityGates,
    BinaryWeeklyWalkForwardConfig,
    PairedProbabilityComparisonConfig,
    build_binary_weekly_walk_forward,
    build_gate_rows,
    build_null_test_rows,
    build_prediction_metrics,
    build_reliability_rows,
    compare_paired_oos_probabilities,
)

PROTOCOL_FREEZE_ID = "residual_absorption_internal_is_paired_wfa_v1"
LABEL_SCHEMA_VERSION = "residual_absorption_positive_catchup_60m_v1"
DEVELOPMENT_START_MS = int(pd.Timestamp("2025-06-03T00:00:00Z").timestamp() * 1_000)
EVALUATION_START_MS = int(pd.Timestamp("2025-08-04T00:00:00Z").timestamp() * 1_000)
EVALUATION_END_MS = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1_000)
RECURRENCE_GAP_MS = 120 * 60_000

_CATEGORICAL_FEATURES = (
    "session_name",
    "candidate_channel",
    "impulse_direction",
    "reference_confirmed",
    "core_eligible",
    "activity_expansion_eligible",
)
_FORBIDDEN_MODEL_COLUMNS = {
    "event_id",
    "symbol",
    "snapshot_time_ms",
    "feature_cutoff_time_ms",
    "session_seq",
    "calendar_month",
    "aggtrades_primary_complete",
    *_CATEGORICAL_FEATURES,
}
_HIGH_RESOLUTION_EXCLUDED_SUFFIXES = ("_missing", "_minute_count", "_coverage")


def build_paired_wfa_input(*, stage1_dir: str | Path) -> tuple[pd.DataFrame, tuple[str, ...], tuple[str, ...]]:
    """Create a label-separated causal frame and the two locked feature sets."""

    root = Path(stage1_dir)
    features = pd.read_parquet(root / "stage2_model_features_with_high_resolution_is.parquet")
    labels = pd.read_parquet(root / "stage1_trait_labels_is.parquet")
    frame = features.merge(
        labels[
            [
                "event_id",
                "symbol",
                "event_snapshot_time_ms",
                "resolution_time_ms",
                "response_win_60m",
            ]
        ],
        on=["event_id", "symbol"],
        how="inner",
        validate="one_to_one",
    )
    if bool((frame["event_snapshot_time_ms"] != frame["snapshot_time_ms"]).any()):
        raise ValueError("WFA labels do not align to feature snapshots")
    frame = frame.loc[frame["aggtrades_primary_complete"].astype(bool)].copy()
    recurrence = _event_recurrence_chains(frame[["event_id", "snapshot_time_ms"]])
    frame = frame.merge(recurrence, on="event_id", how="left", validate="many_to_one")
    frame["group"] = frame["event_id"].astype(str) + "|" + frame["symbol"].astype(str)
    frame["nature_future_start_time_ms"] = frame["snapshot_time_ms"] + 5 * 60_000
    frame["nature_resolution_time_ms"] = frame["resolution_time_ms"].astype("int64")
    frame["nature_y"] = frame["response_win_60m"].astype("int8")
    frame["nature_label_available"] = True
    frame["nature_label_schema_version"] = LABEL_SCHEMA_VERSION
    frame["is_nature_anchor"] = True

    numeric = tuple(
        sorted(
            column
            for column in features.columns
            if column not in _FORBIDDEN_MODEL_COLUMNS
            and not column.startswith("aggtrades_")
            and pd.api.types.is_numeric_dtype(features[column])
        )
    )
    high_resolution = tuple(
        sorted(
            column
            for column in features.columns
            if column.startswith("aggtrades_")
            and column != "aggtrades_primary_complete"
            and not column.endswith(_HIGH_RESOLUTION_EXCLUDED_SUFFIXES)
            and pd.api.types.is_numeric_dtype(features[column])
        )
    )
    if not numeric or not high_resolution:
        raise ValueError("paired WFA feature families are empty")
    forbidden = {"response_win_60m", "resolution_time_ms", "event_snapshot_time_ms"}
    if forbidden.intersection(numeric) or forbidden.intersection(high_resolution):
        raise ValueError("future label columns entered WFA features")
    return frame, numeric, high_resolution


def _event_recurrence_chains(events: pd.DataFrame) -> pd.DataFrame:
    ordered = events.drop_duplicates("event_id").sort_values(
        ["snapshot_time_ms", "event_id"], kind="mergesort"
    )
    new_chain = ordered["snapshot_time_ms"].diff().fillna(RECURRENCE_GAP_MS + 1).gt(
        RECURRENCE_GAP_MS
    )
    chain_number = new_chain.cumsum().astype(int)
    return pd.DataFrame(
        {
            "event_id": ordered["event_id"].astype(str),
            "recurrence_chain_id": "market-recurrence:" + chain_number.astype(str),
        }
    )


def _config(*, arm: str, numeric_features: tuple[str, ...]) -> BinaryWeeklyWalkForwardConfig:
    return BinaryWeeklyWalkForwardConfig(
        protocol_freeze_id=f"{PROTOCOL_FREEZE_ID}:{arm}",
        strategy_name="residual_absorption",
        target_name="positive_direction_adjusted_residual_catchup_60m",
        development_start_ms=DEVELOPMENT_START_MS,
        oos_start_ms=EVALUATION_START_MS,
        oos_end_ms=EVALUATION_END_MS,
        numeric_features=numeric_features,
        categorical_features=_CATEGORICAL_FEATURES,
        breakdown_columns=("session_name", "candidate_channel", "impulse_direction"),
        group_column="group",
        split_group_column="recurrence_chain_id",
        future_start_time_column="nature_future_start_time_ms",
        resolution_time_column="nature_resolution_time_ms",
        label_column="nature_y",
        label_available_column="nature_label_available",
        row_filter_column="is_nature_anchor",
        required_label_schema_column="nature_label_schema_version",
        required_label_schema_value=LABEL_SCHEMA_VERSION,
        label_only_columns=(
            "nature_y",
            "nature_resolution_time_ms",
            "response_win_60m",
            "resolution_time_ms",
        ),
        min_train_rows=5_000,
        min_split_rows=500,
        min_class_rows_per_split=100,
        catboost_iterations=300,
        catboost_depth=5,
        catboost_learning_rate=0.05,
        catboost_l2_leaf_reg=5.0,
        catboost_early_stopping_rounds=30,
        catboost_thread_count=4,
        random_seed=20250804,
        calibration_method=QUANTILE_BINNED_BETA_ISOTONIC_V2,
        isotonic_fit_bins=20,
        isotonic_beta_prior_strength=20.0,
        null_permutations=999,
        gates=BinaryProbabilityGates(
            min_oos_rows=10_000,
            max_skipped_oos_week_fraction=0.0,
            min_auc=0.60,
            min_log_loss_improvement=0.005,
            min_brier_improvement=0.002,
            max_ece=0.05,
            max_auc_permutation_p_value=0.05,
            max_log_loss_permutation_p_value=0.05,
            high_probability_threshold=0.70,
            min_high_probability_rows=100,
            min_high_probability_observed_rate=0.65,
            min_high_probability_wilson_lower_95=0.60,
            max_high_probability_calibration_gap=0.08,
        ),
    )


def run_stage3_paired_wfa(*, stage1_dir: str | Path) -> Path:
    root = Path(stage1_dir)
    output = root / "stage3_internal_is_paired_wfa_v1"
    if output.exists():
        raise FileExistsError(f"paired WFA output already exists: {output}")
    frame, coarse_features, high_resolution_features = build_paired_wfa_input(
        stage1_dir=root
    )
    coarse_config = _config(arm="coarse", numeric_features=coarse_features)
    augmented_config = _config(
        arm="coarse_plus_aggtrades",
        numeric_features=(*coarse_features, *high_resolution_features),
    )
    coarse = build_binary_weekly_walk_forward(frame, coarse_config)
    augmented = build_binary_weekly_walk_forward(frame, augmented_config)
    comparison_config = PairedProbabilityComparisonConfig(
        protocol_freeze_id=PROTOCOL_FREEZE_ID,
        bootstrap_iterations=2_000,
        sign_flip_iterations=999,
        alpha=1.0 / 60.0,
        min_auc_delta=0.01,
        min_log_loss_improvement=0.002,
        min_brier_improvement=0.001,
        random_seed=20250804,
    )
    comparison = compare_paired_oos_probabilities(
        coarse.predictions, augmented.predictions, comparison_config
    )
    output.mkdir(parents=True)
    frame.to_parquet(output / "paired_wfa_input_is.parquet", index=False, compression="zstd")
    for arm, result, config in (
        ("coarse", coarse, coarse_config),
        ("coarse_plus_aggtrades", augmented, augmented_config),
    ):
        arm_dir = output / arm
        arm_dir.mkdir()
        metrics = build_prediction_metrics(result.predictions, config)
        reliability = build_reliability_rows(result.predictions, config)
        null_tests = build_null_test_rows(result.predictions, config)
        gates = build_gate_rows(
            result.predictions,
            metrics,
            reliability,
            null_tests,
            result.weekly_metadata,
            config,
        )
        result.predictions.to_parquet(
            arm_dir / "internal_is_predictions.parquet", index=False, compression="zstd"
        )
        result.weekly_metadata.to_csv(arm_dir / "weekly_metadata.csv", index=False)
        result.feature_importance.to_csv(arm_dir / "feature_importance.csv", index=False)
        metrics.to_csv(arm_dir / "prediction_metrics.csv", index=False)
        reliability.to_csv(arm_dir / "reliability.csv", index=False)
        null_tests.to_csv(arm_dir / "null_tests.csv", index=False)
        gates.to_csv(arm_dir / "gates.csv", index=False)
        (arm_dir / "config.json").write_text(
            json.dumps(asdict(config), indent=2), encoding="utf-8"
        )
    comparison.metrics.to_csv(output / "paired_metrics.csv", index=False)
    comparison.inference.to_csv(output / "paired_inference.csv", index=False)
    comparison.gates.to_csv(output / "paired_gates.csv", index=False)
    report = {
        "protocol_freeze_id": PROTOCOL_FREEZE_ID,
        "partition": "internal_is_walk_forward",
        "untouched_2026_oos_accessed": False,
        "input_row_count": len(frame),
        "input_min_snapshot_time_ms": int(frame["snapshot_time_ms"].min()),
        "input_max_snapshot_time_ms": int(frame["snapshot_time_ms"].max()),
        "coarse_feature_count": len(coarse_features),
        "aggtrades_feature_count": len(high_resolution_features),
        "coarse_prediction_count": len(coarse.predictions),
        "augmented_prediction_count": len(augmented.predictions),
        "coarse_frozen_week_count": len(coarse.frozen_models),
        "augmented_frozen_week_count": len(augmented.frozen_models),
        "paired_population_identical": bool(
            len(coarse.predictions) == len(augmented.predictions)
        ),
        "latest_coarse_train_resolution_before_freeze": _temporal_metadata_pass(
            coarse.weekly_metadata
        ),
        "latest_augmented_train_resolution_before_freeze": _temporal_metadata_pass(
            augmented.weekly_metadata
        ),
        "all_incremental_gates_pass": bool(
            len(comparison.gates) and comparison.gates["status"].eq("PASS").all()
        ),
        "paired_metrics": comparison.metrics.to_dict(orient="records"),
        "paired_inference": comparison.inference.to_dict(orient="records"),
        "paired_gates": comparison.gates.to_dict(orient="records"),
        "scientific_scope": (
            "Probability discrimination/calibration inside IS only; no EV, trade, or PnL claim."
        ),
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return output


def _temporal_metadata_pass(metadata: pd.DataFrame) -> bool:
    scored = metadata.loc[metadata["status"].eq("FROZEN_AND_SCORED")]
    return bool(
        len(scored)
        and (
            scored["latest_train_resolution_time_ms"]
            < scored["weekly_model_freeze_time_ms"]
        ).all()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run paired internal-IS aggTrades WFA.")
    parser.add_argument(
        "--stage1-dir",
        type=Path,
        default=Path(".output/research/residual_absorption/stage1_is_v1"),
    )
    args = parser.parse_args()
    print(run_stage3_paired_wfa(stage1_dir=args.stage1_dir))


__all__ = ["build_paired_wfa_input", "run_stage3_paired_wfa"]


if __name__ == "__main__":
    main()
