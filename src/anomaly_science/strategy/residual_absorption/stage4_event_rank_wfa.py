"""Optimized weekly event-query ranking for residual catch-up."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRanker, Pool
from scipy.stats import spearmanr

from anomaly_science.strategy.residual_absorption.stage3_full_coarse_wfa import (
    build_full_coarse_wfa_input,
)
from anomaly_science.strategy.residual_absorption.stage3_paired_wfa import (
    DEVELOPMENT_START_MS,
    EVALUATION_END_MS,
    EVALUATION_START_MS,
)

RANK_PROTOCOL_FREEZE_ID = "residual_absorption_event_rank_wfa_v1"
CATEGORICAL_FEATURES = (
    "session_name",
    "candidate_channel",
    "impulse_direction",
    "reference_confirmed",
    "core_eligible",
    "activity_expansion_eligible",
)
PRIMARY_METRICS = (
    "event_spearman",
    "top_bottom_catchup_spread",
    "top_bottom_win_rate_gap",
)


@dataclass(frozen=True, slots=True)
class EventRankWFAConfig:
    development_start_ms: int = DEVELOPMENT_START_MS
    evaluation_start_ms: int = EVALUATION_START_MS
    evaluation_end_ms: int = EVALUATION_END_MS
    fit_fraction: float = 0.80
    minimum_train_rows: int = 20_000
    minimum_fit_events: int = 200
    minimum_validation_events: int = 30
    iterations: int = 180
    depth: int = 5
    learning_rate: float = 0.05
    l2_leaf_reg: float = 8.0
    early_stopping_rounds: int = 20
    thread_count: int = 8
    random_seed: int = 20250804
    bootstrap_iterations: int = 2_000
    sign_flip_iterations: int = 999

    def __post_init__(self) -> None:
        if not 0.0 < self.fit_fraction < 1.0:
            raise ValueError("rank WFA fit_fraction must lie in (0, 1)")
        if min(
            self.minimum_train_rows,
            self.minimum_fit_events,
            self.minimum_validation_events,
            self.iterations,
            self.depth,
            self.early_stopping_rounds,
            self.thread_count,
        ) <= 0:
            raise ValueError("rank WFA counts must be positive")
        if self.bootstrap_iterations < 1_000 or self.sign_flip_iterations < 999:
            raise ValueError("rank inference repetition counts are too small")


@dataclass(frozen=True, slots=True)
class EventRankWFAResult:
    predictions: pd.DataFrame
    weekly_metadata: pd.DataFrame
    feature_importance: pd.DataFrame


def build_event_rank_input(
    *, stage1_dir: str | Path
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    root = Path(stage1_dir)
    frame, numeric_features = build_full_coarse_wfa_input(stage1_dir=root)
    outcomes = pd.read_parquet(
        root / "response_outcomes_is.parquet",
        columns=["event_id", "symbol", "catchup_residual_change_60m"],
    )
    frame = frame.merge(outcomes, on=["event_id", "symbol"], validate="one_to_one")
    if frame["catchup_residual_change_60m"].isna().any():
        raise ValueError("event rank target contains unresolved outcomes")
    frame["rank_target"] = frame.groupby("event_id", sort=False)[
        "catchup_residual_change_60m"
    ].rank(method="average", pct=True)
    if bool((frame["feature_cutoff_time_ms"] > frame["snapshot_time_ms"]).any()):
        raise ValueError("event rank feature cutoff exceeds snapshot")
    if bool((frame["nature_future_start_time_ms"] <= frame["snapshot_time_ms"]).any()):
        raise ValueError("event rank future label does not start after snapshot")
    if set(numeric_features).intersection(
        {"rank_target", "catchup_residual_change_60m", "response_win_60m"}
    ):
        raise ValueError("future rank label entered model features")
    return frame, numeric_features


def build_event_rank_weekly_wfa(
    frame: pd.DataFrame,
    *,
    numeric_features: tuple[str, ...],
    categorical_features: tuple[str, ...] = CATEGORICAL_FEATURES,
    config: EventRankWFAConfig | None = None,
) -> EventRankWFAResult:
    active = config or EventRankWFAConfig()
    required = {
        "event_id",
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "nature_resolution_time_ms",
        "recurrence_chain_id",
        "rank_target",
        "catchup_residual_change_60m",
        "session_name",
        "candidate_channel",
        "impulse_direction",
        *numeric_features,
        *categorical_features,
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"event rank input missing columns: {missing}")
    work = frame.copy()
    for column in numeric_features:
        work[column] = pd.to_numeric(work[column], errors="raise")
        if np.isinf(work[column].to_numpy(dtype=float)).any():
            raise ValueError(f"event rank feature contains infinity: {column}")
    for column in categorical_features:
        work[column] = work[column].astype("string").fillna("__MISSING__").astype(str)
    evaluation = work.loc[
        work["snapshot_time_ms"].ge(active.evaluation_start_ms)
        & work["snapshot_time_ms"].lt(active.evaluation_end_ms)
    ].copy()
    evaluation["test_week"] = pd.to_datetime(
        evaluation["snapshot_time_ms"], unit="ms", utc=True
    ).dt.strftime("%G-W%V")

    prediction_parts: list[pd.DataFrame] = []
    metadata: list[dict[str, object]] = []
    importance: list[dict[str, object]] = []
    for test_week, test in evaluation.groupby("test_week", sort=True):
        freeze_ms = _week_start_ms(str(test_week))
        test_chains = set(test["recurrence_chain_id"].astype(str))
        train = work.loc[
            work["snapshot_time_ms"].ge(active.development_start_ms)
            & work["nature_resolution_time_ms"].lt(freeze_ms)
            & ~work["recurrence_chain_id"].astype(str).isin(test_chains)
        ].copy()
        base = {
            "test_week": str(test_week),
            "weekly_model_freeze_time_ms": freeze_ms,
            "test_row_count": len(test),
            "test_event_count": test["event_id"].nunique(),
            "eligible_train_row_count": len(train),
            "eligible_train_event_count": train["event_id"].nunique(),
            "latest_train_resolution_time_ms": (
                int(train["nature_resolution_time_ms"].max()) if len(train) else pd.NA
            ),
        }
        if len(train) < active.minimum_train_rows:
            metadata.append({**base, "status": "SKIPPED", "reason": "minimum_train_rows"})
            continue
        split = _chronological_chain_split(train, fit_fraction=active.fit_fraction)
        fit, validation = split
        if (
            fit["event_id"].nunique() < active.minimum_fit_events
            or validation["event_id"].nunique() < active.minimum_validation_events
        ):
            metadata.append({**base, "status": "SKIPPED", "reason": "minimum_split_events"})
            continue
        fit_pool, fit_order = _rank_pool(
            fit,
            numeric_features=numeric_features,
            categorical_features=categorical_features,
        )
        validation_pool, validation_order = _rank_pool(
            validation,
            numeric_features=numeric_features,
            categorical_features=categorical_features,
        )
        test_pool, test_order = _rank_pool(
            test,
            numeric_features=numeric_features,
            categorical_features=categorical_features,
        )
        model = CatBoostRanker(
            loss_function="QueryRMSE",
            eval_metric="QueryRMSE",
            iterations=active.iterations,
            depth=active.depth,
            learning_rate=active.learning_rate,
            l2_leaf_reg=active.l2_leaf_reg,
            random_seed=active.random_seed,
            thread_count=active.thread_count,
            allow_writing_files=False,
            verbose=False,
        )
        model.fit(
            fit_pool,
            eval_set=validation_pool,
            early_stopping_rounds=active.early_stopping_rounds,
            use_best_model=True,
            verbose=False,
        )
        scored = test_order[
            [
                "event_id",
                "symbol",
                "snapshot_time_ms",
                "feature_cutoff_time_ms",
                "nature_resolution_time_ms",
                "session_name",
                "candidate_channel",
                "impulse_direction",
                "catchup_residual_change_60m",
                "rank_target",
                "event_underreaction_percentile",
            ]
        ].copy()
        scored["test_week"] = str(test_week)
        scored["weekly_model_freeze_time_ms"] = freeze_ms
        scored["model_score"] = model.predict(test_pool)
        scored["baseline_score"] = scored["event_underreaction_percentile"]
        prediction_parts.append(scored)
        best_iteration = int(model.get_best_iteration())
        metadata.append(
            {
                **base,
                "status": "FROZEN_AND_SCORED",
                "reason": "",
                "fit_row_count": len(fit_order),
                "fit_event_count": fit_order["event_id"].nunique(),
                "validation_row_count": len(validation_order),
                "validation_event_count": validation_order["event_id"].nunique(),
                "best_iteration": best_iteration,
                "feature_count": len(numeric_features) + len(categorical_features),
            }
        )
        for name, value in zip(
            (*numeric_features, *categorical_features),
            model.get_feature_importance(type="PredictionValuesChange"),
            strict=True,
        ):
            importance.append(
                {
                    "test_week": str(test_week),
                    "feature_name": name,
                    "importance": float(value),
                }
            )
    predictions = pd.concat(prediction_parts, ignore_index=True) if prediction_parts else pd.DataFrame()
    return EventRankWFAResult(
        predictions=predictions,
        weekly_metadata=pd.DataFrame(metadata),
        feature_importance=pd.DataFrame(importance),
    )


def _chronological_chain_split(
    frame: pd.DataFrame, *, fit_fraction: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    chain_times = frame.groupby("recurrence_chain_id")["snapshot_time_ms"].min().sort_values()
    cutoff = max(1, min(len(chain_times) - 1, int(np.floor(len(chain_times) * fit_fraction))))
    fit_chains = set(chain_times.index[:cutoff])
    return (
        frame.loc[frame["recurrence_chain_id"].isin(fit_chains)].copy(),
        frame.loc[~frame["recurrence_chain_id"].isin(fit_chains)].copy(),
    )


def _rank_pool(
    frame: pd.DataFrame,
    *,
    numeric_features: tuple[str, ...],
    categorical_features: tuple[str, ...],
) -> tuple[Pool, pd.DataFrame]:
    ordered = frame.sort_values(
        ["snapshot_time_ms", "event_id", "symbol"], kind="mergesort"
    ).reset_index(drop=True)
    feature_names = (*numeric_features, *categorical_features)
    matrix = ordered.loc[:, feature_names]
    group_size = ordered.groupby("event_id")["event_id"].transform("size").to_numpy(dtype=float)
    group_weight = 1.0 / group_size
    cat_indices = [matrix.columns.get_loc(column) for column in categorical_features]
    pool = Pool(
        matrix,
        label=ordered["rank_target"].to_numpy(dtype=float),
        group_id=ordered["event_id"].astype(str).to_numpy(),
        group_weight=group_weight,
        cat_features=cat_indices,
        feature_names=list(feature_names),
    )
    return pool, ordered


def build_event_rank_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for event_id, group in predictions.groupby("event_id", sort=True):
        row: dict[str, object] = {
            "event_id": event_id,
            "test_week": str(group["test_week"].iloc[0]),
            "snapshot_time_ms": int(group["snapshot_time_ms"].iloc[0]),
            "session_name": str(group["session_name"].iloc[0]),
            "impulse_direction": int(group["impulse_direction"].iloc[0]),
            "row_count": len(group),
        }
        for arm, score_column in (
            ("model", "model_score"),
            ("baseline", "baseline_score"),
        ):
            values = group["catchup_residual_change_60m"].to_numpy(dtype=float)
            scores = group[score_column].to_numpy(dtype=float)
            spearman = spearmanr(scores, values).statistic if np.unique(scores).size > 1 else np.nan
            order = np.argsort(scores, kind="mergesort")
            count = max(1, int(np.floor(len(group) * 0.20)))
            bottom = values[order[:count]]
            top = values[order[-count:]]
            row[f"{arm}_event_spearman"] = float(spearman)
            row[f"{arm}_top_bottom_catchup_spread"] = float(np.mean(top) - np.mean(bottom))
            row[f"{arm}_top_bottom_win_rate_gap"] = float(np.mean(top > 0.0) - np.mean(bottom > 0.0))
        for metric in PRIMARY_METRICS:
            row[f"delta_{metric}"] = float(row[f"model_{metric}"]) - float(
                row[f"baseline_{metric}"]
            )
        rows.append(row)
    return pd.DataFrame(rows)


def build_rank_inference(
    event_metrics: pd.DataFrame,
    *,
    config: EventRankWFAConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    active = config or EventRankWFAConfig()
    rng = np.random.default_rng(active.random_seed)
    weeks = np.asarray(sorted(event_metrics["test_week"].unique()), dtype=object)
    series = {
        **{f"model_{metric}": f"model_{metric}" for metric in PRIMARY_METRICS},
        **{f"delta_{metric}": f"delta_{metric}" for metric in PRIMARY_METRICS},
    }
    rows = []
    for name, column in series.items():
        observed = float(event_metrics[column].mean())
        bootstrap = np.empty(active.bootstrap_iterations, dtype=float)
        for iteration in range(active.bootstrap_iterations):
            sampled = rng.choice(weeks, size=len(weeks), replace=True)
            values = np.concatenate(
                [event_metrics.loc[event_metrics["test_week"].eq(week), column].to_numpy(dtype=float) for week in sampled]
            )
            bootstrap[iteration] = float(np.mean(values))
        weekly = event_metrics.groupby("test_week")[column].mean().reindex(weeks).to_numpy(dtype=float)
        null = np.empty(active.sign_flip_iterations, dtype=float)
        for iteration in range(active.sign_flip_iterations):
            signs = np.where(rng.random(len(weekly)) < 0.5, -1.0, 1.0)
            null[iteration] = float(np.mean(weekly * signs))
        rows.append(
            {
                "estimand": name,
                "observed": observed,
                "bootstrap_ci_lower_95": float(np.quantile(bootstrap, 0.025)),
                "bootstrap_ci_upper_95": float(np.quantile(bootstrap, 0.975)),
                "week_sign_flip_p_value": float(
                    (1 + np.sum(null >= observed)) / (active.sign_flip_iterations + 1)
                ),
            }
        )
    inference = pd.DataFrame(rows)
    minimums = {
        "model_event_spearman": 0.05,
        "model_top_bottom_catchup_spread": 0.0010,
        "model_top_bottom_win_rate_gap": 0.03,
        "delta_event_spearman": 0.01,
        "delta_top_bottom_catchup_spread": 0.0002,
        "delta_top_bottom_win_rate_gap": 0.01,
    }
    gates = []
    for estimand, minimum in minimums.items():
        row = inference.loc[inference["estimand"].eq(estimand)].iloc[0]
        for suffix, observed, operator, required in (
            ("minimum", float(row["observed"]), ">=", minimum),
            ("ci_lower_positive", float(row["bootstrap_ci_lower_95"]), ">", 0.0),
            ("sign_flip_p", float(row["week_sign_flip_p_value"]), "<=", 0.05),
        ):
            passed = observed >= required if operator == ">=" else observed > required if operator == ">" else observed <= required
            gates.append(
                {
                    "gate": f"{estimand}_{suffix}",
                    "observed": observed,
                    "operator": operator,
                    "required": required,
                    "status": "PASS" if passed else "FAIL",
                }
            )
    return inference, pd.DataFrame(gates)


def run_stage4_event_rank_wfa(*, stage1_dir: str | Path) -> Path:
    root = Path(stage1_dir)
    output = root / "stage4_event_rank_wfa_v1"
    if output.exists():
        raise FileExistsError(f"event rank WFA output already exists: {output}")
    config = EventRankWFAConfig()
    frame, numeric_features = build_event_rank_input(stage1_dir=root)
    result = build_event_rank_weekly_wfa(
        frame, numeric_features=numeric_features, config=config
    )
    event_metrics = build_event_rank_metrics(result.predictions)
    inference, gates = build_rank_inference(event_metrics, config=config)
    output.mkdir(parents=True)
    result.predictions.to_parquet(output / "rank_predictions_is.parquet", index=False, compression="zstd")
    event_metrics.to_parquet(output / "event_rank_metrics_is.parquet", index=False, compression="zstd")
    result.weekly_metadata.to_csv(output / "weekly_metadata.csv", index=False)
    result.feature_importance.to_csv(output / "feature_importance.csv", index=False)
    inference.to_csv(output / "rank_inference.csv", index=False)
    gates.to_csv(output / "rank_gates.csv", index=False)
    symbol = _symbol_rank_diagnostics(result.predictions)
    symbol.to_parquet(output / "symbol_rank_diagnostics_is.parquet", index=False, compression="zstd")
    scored = result.weekly_metadata.loc[result.weekly_metadata["status"].eq("FROZEN_AND_SCORED")]
    report = {
        "protocol_freeze_id": RANK_PROTOCOL_FREEZE_ID,
        "partition": "exploratory_internal_is_walk_forward",
        "untouched_2026_oos_accessed": False,
        "input_row_count": len(frame),
        "numeric_feature_count": len(numeric_features),
        "categorical_feature_count": len(CATEGORICAL_FEATURES),
        "prediction_row_count": len(result.predictions),
        "prediction_event_count": result.predictions["event_id"].nunique(),
        "frozen_week_count": len(scored),
        "skipped_week_count": int(result.weekly_metadata["status"].eq("SKIPPED").sum()),
        "latest_train_resolution_before_freeze": bool(
            len(scored)
            and (scored["latest_train_resolution_time_ms"] < scored["weekly_model_freeze_time_ms"]).all()
        ),
        "all_rank_gates_pass": bool(len(gates) and gates["status"].eq("PASS").all()),
        "inference": inference.to_dict(orient="records"),
        "gates": gates.to_dict(orient="records"),
        "config": asdict(config),
        "scientific_scope": "Relative response ranking inside IS; no EV, trade, or PnL claim.",
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output


def _symbol_rank_diagnostics(predictions: pd.DataFrame) -> pd.DataFrame:
    work = predictions.copy()
    work["predicted_percentile"] = work.groupby("event_id")["model_score"].rank(pct=True)
    work["predicted_top_quintile"] = work["predicted_percentile"].gt(0.80)
    work["actual_top_quintile"] = work["rank_target"].gt(0.80)
    return work.groupby("symbol", sort=True).agg(
        row_count=("symbol", "size"),
        predicted_top_count=("predicted_top_quintile", "sum"),
        predicted_top_share=("predicted_top_quintile", "mean"),
        actual_top_share=("actual_top_quintile", "mean"),
        mean_rank_target=("rank_target", "mean"),
        mean_catchup_60m=("catchup_residual_change_60m", "mean"),
    ).reset_index()


def _week_start_ms(week: str) -> int:
    return int(pd.Timestamp.fromisocalendar(int(week[:4]), int(week[-2:]), 1).tz_localize("UTC").timestamp() * 1_000)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run event-level residual rank WFA.")
    parser.add_argument(
        "--stage1-dir",
        type=Path,
        default=Path(".output/research/residual_absorption/stage1_is_v1"),
    )
    args = parser.parse_args()
    print(run_stage4_event_rank_wfa(stage1_dir=args.stage1_dir))


__all__ = [
    "EventRankWFAConfig",
    "build_event_rank_input",
    "build_event_rank_metrics",
    "build_event_rank_weekly_wfa",
    "build_rank_inference",
    "run_stage4_event_rank_wfa",
]


if __name__ == "__main__":
    main()
