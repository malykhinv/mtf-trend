"""Strict weekly-forward expected-R model for triple-tap IS research.

The target is the realised R-multiple from point-in-time structural stop/take
simulation.  This avoids fixed-percent future targets and aligns prediction
with the quantity used by the economic decision.  Transaction costs remain a
decision-time adjustment because they are known from structural stop distance.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.metrics import mean_absolute_error

from anomaly_science.strategy.triple_tap.research import TRADE_KEY
from anomaly_science.strategy.triple_tap.research.model import (
    CAT_FEATURES,
    FEATURES,
    ID_COLS,
    LABELS_CLOSE,
    VOL_LEAKY,
)

TARGET = "r_multiple"
TARGET_RESOLUTION = "outcome_time_ms"
OUT = Path(".output/results/triple_tap_v1/ev_walkforward.parquet")
SHUFFLED_OUT = Path(".output/results/triple_tap_v1/ev_walkforward_shuffled.parquet")
PARAMS = dict(
    iterations=250,
    depth=4,
    learning_rate=0.05,
    l2_leaf_reg=6.0,
    loss_function="RMSE",
    random_seed=7,
    thread_count=4,
    verbose=False,
    allow_writing_files=False,
)


def run_walk_forward(
    labels_path: Path = LABELS_CLOSE,
    out_path: Path = OUT,
    *,
    min_train_weeks: int = 10,
    shuffle_train_target: bool = False,
) -> pd.DataFrame:
    features = pd.read_parquet(FEATURES)
    labels = pd.read_parquet(labels_path)[TRADE_KEY + [TARGET, TARGET_RESOLUTION]]
    frame = features.merge(labels, on=TRADE_KEY, how="inner", validate="one_to_one")
    frame = frame[frame[TARGET].notna()].reset_index(drop=True)

    # The confirmed breakout bar is closed at the close-entry decision, so its
    # metrics are causal.  Aggregated 4h regime columns remain excluded because
    # their builder can include an incomplete 4h bucket.
    X = frame.drop(
        columns=ID_COLS + [TARGET, TARGET_RESOLUTION] + VOL_LEAKY,
        errors="ignore",
    )
    for col in CAT_FEATURES:
        X[col] = X[col].astype(str)
    cat_idx = [X.columns.get_loc(c) for c in CAT_FEATURES if c in X.columns]

    stamps = pd.to_datetime(frame["entry_time_ms"], unit="ms", utc=True)
    week_start = stamps.dt.floor("D") - pd.to_timedelta(stamps.dt.dayofweek, unit="D")
    weeks = sorted(week_start.unique())
    predictions: list[pd.DataFrame] = []
    for week_no, test_start in enumerate(weeks):
        if week_no < min_train_weeks:
            continue
        freeze_ms = int(pd.Timestamp(test_start).timestamp() * 1000)
        train = frame[TARGET_RESOLUTION].astype("int64") <= freeze_ms
        test = week_start == test_start
        if not train.any() or not test.any():
            continue
        model = CatBoostRegressor(**PARAMS)
        train_target = frame.loc[train, TARGET].to_numpy(copy=True)
        if shuffle_train_target:
            train_target = np.random.default_rng(10_000 + week_no).permutation(train_target)
        model.fit(Pool(X.loc[train], train_target, cat_features=cat_idx), verbose=False)
        idx = frame.index[test]
        part = frame.loc[idx, TRADE_KEY + [TARGET]].copy()
        part["model_score_r"] = model.predict(X.loc[idx])
        part["model_week"] = pd.Timestamp(test_start).strftime("%G-W%V")
        part["model_freeze_time_ms"] = freeze_ms
        part["train_n"] = int(train.sum())
        part["train_max_target_resolution_time_ms"] = int(frame.loc[train, TARGET_RESOLUTION].max())
        predictions.append(part)
        print(
            f"  week {part['model_week'].iloc[0]} train={int(train.sum())} test={int(test.sum())}",
            flush=True,
        )

    if not predictions:
        raise ValueError("expected-R walk-forward produced no supported weeks")
    result = pd.concat(predictions, ignore_index=True)
    spearman = float(result[TARGET].corr(result["model_score_r"], method="spearman"))
    mae = float(mean_absolute_error(result[TARGET], result["model_score_r"]))
    result.drop(columns=[TARGET]).to_parquet(out_path, index=False)
    print(
        f"strict {'SHUFFLED ' if shuffle_train_target else ''}expected-R forward "
        f"rows={len(result)} weeks={result['model_week'].nunique()} "
        f"Spearman={spearman:.3f} MAE={mae:.3f}R -> {out_path}",
        flush=True,
    )
    return result


if __name__ == "__main__":
    run_walk_forward()
