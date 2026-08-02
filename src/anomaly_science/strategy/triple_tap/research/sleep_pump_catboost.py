"""Weekly walk-forward CatBoost experiment for the sleep→pump candidate funnel.

The model ranks *already proposed* candidates by the probability that the
expert retained a sleep→pump.  It is deliberately not a market-wide detector,
not a calibrated probability, and not a trading model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score


CATBOOST_EXPERIMENT_VERSION = "sleep_pump_catboost_weekly_oos_v2_2026-07-13"
LEARNING_DIR = Path(".output/results/triple_tap_v1/sleep_pump_review/learning_v1")
FEATURE_COLUMNS = (
    "pump_pct",
    "pump_bars",
    "pump_hours",
    "sleep_range_pct",
    "sleep_directional_drift_pct",
    "base_to_high_pct",
    "pump_path_efficiency",
    "pump_retrace_share",
    "pump_single_bar_range_share",
    "pump_upper_wick_share",
    "pump_over_sleep_vol",
    "pump_over_sleep_trades",
)
TARGET_COLUMN = "expert_has_sleep_pump"


@dataclass(frozen=True, slots=True)
class SleepPumpCatBoostConfig:
    min_train_rows: int = 100
    iterations: int = 80
    depth: int = 3
    learning_rate: float = 0.05
    l2_leaf_reg: float = 20.0
    random_seed: int = 20260713
    thread_count: int = 4
    block_bootstrap_replicates: int = 1_000


@dataclass(frozen=True, slots=True)
class SleepPumpCatBoostSummary:
    total_rows: int
    oos_rows: int
    oos_positive_count: int
    first_oos_week: str | None
    model_roc_auc: float | None
    baseline_roc_auc: float | None
    model_average_precision: float | None
    baseline_average_precision: float | None
    model_brier: float | None
    baseline_brier: float | None
    roc_auc_delta_ci95_low: float | None
    roc_auc_delta_ci95_high: float | None
    average_precision_delta_ci95_low: float | None
    average_precision_delta_ci95_high: float | None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _week_start_ms(times_ms: pd.Series) -> pd.Series:
    timestamps = pd.to_datetime(times_ms, unit="ms", utc=True)
    monday = timestamps.dt.normalize() - pd.to_timedelta(timestamps.dt.dayofweek, unit="D")
    return (monday.astype("int64") // 1_000_000).astype("int64")


def _week_name(start_ms: int) -> str:
    return pd.Timestamp(start_ms, unit="ms", tz="UTC").strftime("%G-W%V")


def _validated_frame(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(FEATURE_COLUMNS + (TARGET_COLUMN, "feature_cutoff_time_ms", "label_observation_end_ms", "score", "event_id", "symbol")) - set(frame.columns)
    if missing:
        raise ValueError(f"learning frame misses required columns: {sorted(missing)}")
    if frame["event_id"].duplicated().any():
        raise ValueError("learning frame must have unique event_id values")
    if (frame["label_observation_end_ms"] < frame["feature_cutoff_time_ms"]).any():
        raise ValueError("label observation must not end before the feature cutoff")
    result = frame.copy()
    result["week_start_ms"] = _week_start_ms(result["feature_cutoff_time_ms"])
    return result.sort_values(["feature_cutoff_time_ms", "event_id"]).reset_index(drop=True)


def _feature_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    # CatBoost natively treats NaN as missing.  Infinity is not a valid numeric
    # measurement and is made explicit as missing instead of silently clipped.
    return frame.loc[:, FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan).astype(float)


def build_weekly_oos_predictions(
    frame: pd.DataFrame,
    *,
    config: SleepPumpCatBoostConfig = SleepPumpCatBoostConfig(),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fit one frozen CatBoost per test week and return only causal OOS rows."""

    if config.min_train_rows <= 0 or config.iterations <= 0 or config.depth <= 0:
        raise ValueError("CatBoost row and tree settings must be positive")
    data = _validated_frame(frame)
    prediction_rows: list[pd.DataFrame] = []
    importance_rows: list[dict[str, float | int | str]] = []

    for week_start in sorted(data["week_start_ms"].unique()):
        test = data.loc[data["week_start_ms"] == week_start].copy()
        # Targets in the training set must have become observable before the
        # test week begins.  This is the purge that protects the time contract.
        train = data.loc[
            (data["feature_cutoff_time_ms"] < week_start)
            & (data["label_observation_end_ms"] < week_start)
        ].copy()
        if len(train) < config.min_train_rows or train[TARGET_COLUMN].nunique() != 2:
            continue

        model = CatBoostClassifier(
            loss_function="Logloss",
            eval_metric="Logloss",
            iterations=config.iterations,
            depth=config.depth,
            learning_rate=config.learning_rate,
            l2_leaf_reg=config.l2_leaf_reg,
            random_seed=config.random_seed,
            thread_count=config.thread_count,
            allow_writing_files=False,
            verbose=False,
        )
        train_x = _feature_matrix(train)
        train_y = train[TARGET_COLUMN].astype(np.int8)
        model.fit(train_x, train_y)
        test_x = _feature_matrix(test)
        prediction = test.loc[
            :,
            ["event_id", "symbol", "feature_cutoff_time_ms", "label_observation_end_ms", TARGET_COLUMN, "score"],
        ].copy()
        prediction["test_week"] = _week_name(int(week_start))
        prediction["train_row_count"] = len(train)
        prediction["catboost_score"] = model.predict_proba(test_x)[:, 1]
        prediction["baseline_score"] = test["score"].astype(float).to_numpy()
        prediction_rows.append(prediction)
        for feature, importance in zip(FEATURE_COLUMNS, model.get_feature_importance(), strict=True):
            importance_rows.append(
                {
                    "test_week": _week_name(int(week_start)),
                    "train_row_count": len(train),
                    "feature": feature,
                    "importance": float(importance),
                }
            )

    columns = [
        "event_id", "symbol", "feature_cutoff_time_ms", "label_observation_end_ms", TARGET_COLUMN,
        "score", "test_week", "train_row_count", "catboost_score", "baseline_score",
    ]
    predictions = pd.concat(prediction_rows, ignore_index=True) if prediction_rows else pd.DataFrame(columns=columns)
    importance = pd.DataFrame(importance_rows, columns=["test_week", "train_row_count", "feature", "importance"])
    return predictions, importance


def _metric_or_none(metric: object, target: np.ndarray, score: np.ndarray) -> float | None:
    if len(target) == 0 or len(np.unique(target)) != 2:
        return None
    return float(metric(target, score))


def _block_bootstrap_delta_ci(
    predictions: pd.DataFrame,
    *,
    metric: object,
    replicates: int,
    random_seed: int,
) -> tuple[float | None, float | None]:
    """Resample complete test weeks so correlated same-week rows stay together."""

    if replicates <= 0:
        return None, None
    grouped = [
        group.loc[:, [TARGET_COLUMN, "catboost_score", "baseline_score"]].to_numpy(dtype=float)
        for _, group in predictions.groupby("test_week", sort=True)
    ]
    if len(grouped) < 2:
        return None, None
    rng = np.random.default_rng(random_seed)
    deltas: list[float] = []
    for _ in range(replicates):
        sampled = np.concatenate([grouped[index] for index in rng.integers(len(grouped), size=len(grouped))])
        target = sampled[:, 0].astype(np.int8)
        if len(np.unique(target)) != 2:
            continue
        deltas.append(float(metric(target, sampled[:, 1]) - metric(target, sampled[:, 2])))
    if not deltas:
        return None, None
    low, high = np.quantile(deltas, [0.025, 0.975])
    return float(low), float(high)


def summarize_oos_predictions(
    predictions: pd.DataFrame,
    *,
    total_rows: int,
    block_bootstrap_replicates: int = 0,
    random_seed: int = 20260713,
) -> SleepPumpCatBoostSummary:
    if predictions.empty:
        return SleepPumpCatBoostSummary(total_rows, 0, 0, None, None, None, None, None, None, None, None, None, None, None)
    target = predictions[TARGET_COLUMN].astype(np.int8).to_numpy()
    catboost_score = predictions["catboost_score"].astype(float).to_numpy()
    baseline_score = predictions["baseline_score"].astype(float).to_numpy()
    roc_auc_ci = _block_bootstrap_delta_ci(
        predictions,
        metric=roc_auc_score,
        replicates=block_bootstrap_replicates,
        random_seed=random_seed,
    )
    average_precision_ci = _block_bootstrap_delta_ci(
        predictions,
        metric=average_precision_score,
        replicates=block_bootstrap_replicates,
        random_seed=random_seed + 1,
    )
    return SleepPumpCatBoostSummary(
        total_rows=total_rows,
        oos_rows=len(predictions),
        oos_positive_count=int(target.sum()),
        first_oos_week=str(predictions["test_week"].iloc[0]),
        model_roc_auc=_metric_or_none(roc_auc_score, target, catboost_score),
        baseline_roc_auc=_metric_or_none(roc_auc_score, target, baseline_score),
        model_average_precision=_metric_or_none(average_precision_score, target, catboost_score),
        baseline_average_precision=_metric_or_none(average_precision_score, target, baseline_score),
        model_brier=_metric_or_none(brier_score_loss, target, catboost_score),
        baseline_brier=None,
        roc_auc_delta_ci95_low=roc_auc_ci[0],
        roc_auc_delta_ci95_high=roc_auc_ci[1],
        average_precision_delta_ci95_low=average_precision_ci[0],
        average_precision_delta_ci95_high=average_precision_ci[1],
    )


def _report(summary: SleepPumpCatBoostSummary) -> str:
    def number(value: float | None) -> str:
        return "not estimable" if value is None else f"{value:.4f}"

    return "\n".join(
        [
            "# Sleep→pump CatBoost weekly OOS experiment",
            "",
            f"- Experiment version: `{CATBOOST_EXPERIMENT_VERSION}`",
            f"- Total exploratory labelled rows: {summary.total_rows}",
            f"- Causal OOS rows: {summary.oos_rows}",
            f"- Causal OOS positives: {summary.oos_positive_count}",
            f"- First OOS week: {summary.first_oos_week or 'not available'}",
            "",
            "| Metric | Current candidate score | CatBoost |",
            "| --- | ---: | ---: |",
            f"| ROC-AUC | {number(summary.baseline_roc_auc)} | {number(summary.model_roc_auc)} |",
            f"| Average precision | {number(summary.baseline_average_precision)} | {number(summary.model_average_precision)} |",
            f"| Brier score | not a probability baseline | {number(summary.model_brier)} |",
            "",
            f"- Block-bootstrap 95% CI for CatBoost minus baseline ROC-AUC: [{number(summary.roc_auc_delta_ci95_low)}, {number(summary.roc_auc_delta_ci95_high)}]",
            f"- Block-bootstrap 95% CI for CatBoost minus baseline average precision: [{number(summary.average_precision_delta_ci95_low)}, {number(summary.average_precision_delta_ci95_high)}]",
            "",
            "## Contract",
            "",
            "One CatBoost is fit once per calendar test week using only rows whose feature cutoff and label-observation horizon precede that week. Scores are OOS rankings inside the existing broad-candidate funnel. They are not calibrated probabilities, do not estimate market-wide prevalence or recall, and must not drive trading or production decisions.",
            "",
        ]
    )


def run_sleep_pump_catboost_experiment(
    *,
    learning_path: Path = LEARNING_DIR / "labeled_events.parquet",
    output_dir: Path = LEARNING_DIR / "catboost_weekly_oos_v2",
    config: SleepPumpCatBoostConfig = SleepPumpCatBoostConfig(),
) -> SleepPumpCatBoostSummary:
    """Run and materialise the bounded weekly walk-forward experiment."""

    frame = pd.read_parquet(learning_path)
    predictions, importance = build_weekly_oos_predictions(frame, config=config)
    summary = summarize_oos_predictions(
        predictions,
        total_rows=len(frame),
        block_bootstrap_replicates=config.block_bootstrap_replicates,
        random_seed=config.random_seed,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(output_dir / "oos_predictions.parquet", index=False)
    importance.to_csv(output_dir / "feature_importance_by_week.csv", index=False)
    aggregate_importance = (
        importance.groupby("feature", as_index=False)["importance"].mean().sort_values("importance", ascending=False)
        if not importance.empty
        else pd.DataFrame(columns=["feature", "importance"])
    )
    aggregate_importance.to_csv(output_dir / "feature_importance_mean.csv", index=False)
    manifest = {
        "experiment_version": CATBOOST_EXPERIMENT_VERSION,
        "config": asdict(config),
        "feature_columns": list(FEATURE_COLUMNS),
        "target_column": TARGET_COLUMN,
        "summary": asdict(summary),
        "source_learning_path": str(learning_path),
        "source_learning_sha256": _sha256(learning_path),
        "claim_boundary": "candidate-funnel OOS ranking only; no calibrated probability, recall, EV, PnL, or trading claim",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output_dir / "report.md").write_text(_report(summary), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the sleep→pump weekly CatBoost OOS experiment.")
    parser.add_argument("--learning-path", type=Path, default=LEARNING_DIR / "labeled_events.parquet")
    parser.add_argument("--output-dir", type=Path, default=LEARNING_DIR / "catboost_weekly_oos_v2")
    args = parser.parse_args()
    summary = run_sleep_pump_catboost_experiment(learning_path=args.learning_path, output_dir=args.output_dir)
    print(
        "sleep-pump CatBoost OOS: "
        f"rows={summary.oos_rows}, auc={summary.model_roc_auc}, baseline_auc={summary.baseline_roc_auc}"
    )


if __name__ == "__main__":
    main()
