"""CatBoost feature-discovery for triple-tap / CAP setups.

Target = ``label_win`` (reached take before stop). Validation is deliberately
paranoid (this project has a long history of look-ahead / one-week-lottery false
positives):
  - GROUP K-fold by ISO week: a whole week is train or test, so temporally
    adjacent setups can't leak and no single pump wave can be memorised.
  - out-of-fold AUC vs TWO controls: the blind base rate, and a shuffled-label
    run (must collapse to ~0.5, else the pipeline is leaking).
  - per-fold AUC is printed so a one-week fluke is visible.
Feature importance = mean |SHAP| out-of-fold. IS ONLY - no OOS here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from anomaly_science.strategy.triple_tap.research import TRADE_KEY

FEATURES = Path(".output/results/triple_tap_v1/features.parquet")
LABELS = Path(".output/results/triple_tap_v1/labels.parquet")
CAT_FEATURES = ["tf", "setup_family", "setup_type"]
ID_COLS = ["symbol", "entry_time_ms", "tf_key"]
PARAMS = dict(iterations=400, depth=4, learning_rate=0.05, l2_leaf_reg=6.0,
              loss_function="Logloss", eval_metric="AUC", random_seed=7, verbose=False)


# Primary target: the point-in-time structural take is reached before the
# point-in-time structural stop.  No fixed-percent future move is promoted to a
# decision target.  ``ran_08`` remains a historical descriptive label only.
TARGET = "label_win"
TARGET_RESOLUTION = "outcome_time_ms"


# 4h-regime volume at entry is ALWAYS look-ahead (the 4h bar isn't complete even
# at the TF bar close). Breakout-BAR stats are look-ahead for a BREAK entry
# (intra-bar) but CAUSAL for a CLOSE entry (bar complete).
VOL_LEAKY = ["vol_pct_rank", "vol_step_ratio", "vol_recent", "vol_base"]
BRK_LEAKY = ["brk_close_over_lvl", "brk_wick", "brk_vol_ratio"]


def _load(labels_path: Path, keep_brk: bool) -> tuple[pd.DataFrame, pd.Series, np.ndarray]:
    feat = pd.read_parquet(FEATURES)
    lab = pd.read_parquet(labels_path)[TRADE_KEY + [TARGET]]
    df = feat.merge(lab, on=TRADE_KEY, how="inner", validate="one_to_one")
    df = df[df[TARGET].notna()].reset_index(drop=True)
    week = pd.to_datetime(df["entry_time_ms"], unit="ms", utc=True).dt.strftime("%G-W%V").values
    y = df[TARGET].astype(int)
    leaky = VOL_LEAKY + ([] if keep_brk else BRK_LEAKY)
    X = df.drop(columns=ID_COLS + [TARGET] + leaky, errors="ignore")
    for c in CAT_FEATURES:
        X[c] = X[c].astype(str)
    return X, y, week


def _oof_auc(X, y, groups, params, n_splits=5) -> tuple[float, list[float], np.ndarray, np.ndarray]:
    cat_idx = [X.columns.get_loc(c) for c in CAT_FEATURES if c in X.columns]
    oof = np.zeros(len(y))
    fold_auc: list[float] = []
    shap_sum = np.zeros(X.shape[1])
    gkf = GroupKFold(n_splits=n_splits)
    for tr, te in gkf.split(X, y, groups):
        m = CatBoostClassifier(**params)
        m.fit(Pool(X.iloc[tr], y.iloc[tr], cat_features=cat_idx), verbose=False)
        oof[te] = m.predict_proba(X.iloc[te])[:, 1]
        if len(np.unique(y.iloc[te])) > 1:
            fold_auc.append(roc_auc_score(y.iloc[te], oof[te]))
        sh = m.get_feature_importance(Pool(X.iloc[te], y.iloc[te], cat_features=cat_idx), type="ShapValues")
        shap_sum += np.abs(sh[:, :-1]).mean(axis=0)
    return roc_auc_score(y, oof), fold_auc, shap_sum / n_splits, oof


def run(labels_path: Path = LABELS, keep_brk: bool = False, tag: str = "break",
        oof_out: Path | None = None) -> None:
    X, y, week = _load(labels_path, keep_brk)
    feat = pd.read_parquet(FEATURES)
    lab = pd.read_parquet(labels_path)[TRADE_KEY + [TARGET]]
    ids = feat.merge(lab, on=TRADE_KEY, how="inner", validate="one_to_one")
    ids = ids[ids[TARGET].notna()].reset_index(drop=True)[TRADE_KEY]
    print(f"\n===== ENTRY = {tag} =====", flush=True)
    print(f"rows {len(y)}  ran08-rate {y.mean():.1%}  weeks {len(set(week))}  features {X.shape[1]}", flush=True)

    auc, folds, shap, oof = _oof_auc(X, y, week, PARAMS)
    if oof_out is not None:
        ids.assign(oof_prob=oof).to_parquet(oof_out, index=False)
    rng = np.random.default_rng(0)
    y_shuf = pd.Series(rng.permutation(y.values), index=y.index)
    auc_shuf, _, _, _ = _oof_auc(X, y_shuf, week, PARAMS)

    print(f"OOF AUC          {auc:.3f}   (blind base rate {y.mean():.1%})", flush=True)
    print(f"shuffled control {auc_shuf:.3f}   (must be ~0.50)", flush=True)
    print(f"per-week-fold AUC {[round(a, 3) for a in folds]}  (watch for one-fold flukes)", flush=True)

    imp = pd.Series(shap, index=X.columns).sort_values(ascending=False)
    print("top 15 features by mean|SHAP|:", flush=True)
    for name, v in imp.head(15).items():
        print(f"  {name:24s} {v:.4f}", flush=True)


LABELS_CLOSE = Path(".output/results/triple_tap_v1/labels_close.parquet")


OOF_OUT = Path(".output/results/triple_tap_v1/oof.parquet")


OOF_CLOSE_OUT = Path(".output/results/triple_tap_v1/oof_close.parquet")

WALK_FORWARD_OUT = Path(".output/results/triple_tap_v1/oof_walkforward.parquet")


def run_walk_forward(
    labels_path: Path = LABELS_CLOSE,
    out_path: Path = WALK_FORWARD_OUT,
    *,
    min_train_weeks: int = 10,
    selection_quantile: float = 0.75,
) -> pd.DataFrame:
    """Strict expanding weekly walk-forward on IS.

    A label is admitted to training only after its exact outcome time.  The
    model is frozen once per test week.  The deployable selection threshold is
    the requested quantile of *prior weeks' forward scores*; the current
    week's score distribution is never used to set its own threshold.
    """
    feat = pd.read_parquet(FEATURES)
    label_cols = TRADE_KEY + [TARGET, TARGET_RESOLUTION]
    lab = pd.read_parquet(labels_path)[label_cols]
    frame = feat.merge(lab, on=TRADE_KEY, how="inner", validate="one_to_one")
    frame = frame[frame[TARGET].notna()].reset_index(drop=True)
    if frame.duplicated(TRADE_KEY).any():
        raise ValueError(f"walk-forward rows violate unique trade identity {TRADE_KEY}")

    leaky = VOL_LEAKY  # close-entry decisions may use the completed breakout bar
    X = frame.drop(columns=ID_COLS + [TARGET, TARGET_RESOLUTION] + leaky, errors="ignore")
    for col in CAT_FEATURES:
        X[col] = X[col].astype(str)
    cat_idx = [X.columns.get_loc(c) for c in CAT_FEATURES if c in X.columns]

    stamps = pd.to_datetime(frame["entry_time_ms"], unit="ms", utc=True)
    week_start = stamps.dt.floor("D") - pd.to_timedelta(stamps.dt.dayofweek, unit="D")
    unique_weeks = sorted(week_start.unique())
    predictions: list[pd.DataFrame] = []
    for week_no, test_start in enumerate(unique_weeks):
        if week_no < min_train_weeks:
            continue
        freeze_ms = int(pd.Timestamp(test_start).timestamp() * 1000)
        train_mask = frame[TARGET_RESOLUTION].astype("int64") <= freeze_ms
        test_mask = week_start == test_start
        if train_mask.sum() == 0 or test_mask.sum() == 0 or frame.loc[train_mask, TARGET].nunique() < 2:
            continue
        model = CatBoostClassifier(**PARAMS)
        model.fit(
            Pool(X.loc[train_mask], frame.loc[train_mask, TARGET].astype(int), cat_features=cat_idx),
            verbose=False,
        )
        idx = frame.index[test_mask]
        part = frame.loc[idx, TRADE_KEY + [TARGET]].copy()
        part["oof_prob"] = model.predict_proba(X.loc[idx])[:, 1]
        part["model_week"] = pd.Timestamp(test_start).strftime("%G-W%V")
        part["model_freeze_time_ms"] = freeze_ms
        part["train_n"] = int(train_mask.sum())
        part["train_max_target_resolution_time_ms"] = int(
            frame.loc[train_mask, TARGET_RESOLUTION].max()
        )
        predictions.append(part)

    if not predictions:
        raise ValueError("walk-forward produced no supported test weeks")
    result = pd.concat(predictions, ignore_index=True)
    result["selection_threshold"] = np.nan
    result["selected_causal"] = False
    for week in result["model_week"].drop_duplicates():
        current = result["model_week"] == week
        prior = result["model_week"] < week
        if not prior.any():
            continue
        threshold = float(result.loc[prior, "oof_prob"].quantile(selection_quantile))
        result.loc[current, "selection_threshold"] = threshold
        result.loc[current, "selected_causal"] = result.loc[current, "oof_prob"] >= threshold

    auc = roc_auc_score(result[TARGET].astype(int), result["oof_prob"])
    result.drop(columns=[TARGET]).to_parquet(out_path, index=False)
    print(
        f"strict weekly forward rows={len(result)} weeks={result['model_week'].nunique()} "
        f"AUC={auc:.3f} causal-selected={int(result['selected_causal'].sum())} -> {out_path}",
        flush=True,
    )
    return result


if __name__ == "__main__":
    run(LABELS, keep_brk=False, tag="break (fill at level, intra-bar)", oof_out=OOF_OUT)
    if LABELS_CLOSE.exists():
        run(LABELS_CLOSE, keep_brk=True, tag="close (fill at confirmed close-above-level)",
            oof_out=OOF_CLOSE_OUT)
        run_walk_forward()
