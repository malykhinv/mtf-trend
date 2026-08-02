"""CatBoost ranker + full correlation read-out for the session-break setup.

Goal (user): squeeze the maximum and *understand every correlation*. Per stratum
(variant x tier) we:
  * fit a week-grouped, out-of-fold CatBoost classifier on the rich feature set,
    report OOF AUC vs a within-week shuffled null and the top-decile MFE lift;
  * rank features by CatBoost importance AND mean |SHAP| (native, no shap pkg);
  * print each feature's Spearman correlation with the outcome (signed edge);
  * print the feature-feature correlation matrix's strongest pairs (redundancy).

Targets: mfe120_atr (long runner) or rev_hit (reverter). Scores are written for
downstream execution selection.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from anomaly_science.strategy.session_break.research.portrait import (
    DEV_END_MS, FEATURES, _clean, _runner_fizzle, add_features,
)

DEFAULT_EVENTS = Path(".output/results/session_break/events.parquet")


def _cb():
    return CatBoostClassifier(
        depth=4, iterations=350, learning_rate=0.04, l2_leaf_reg=6.0,
        loss_function="Logloss", random_seed=0, verbose=False,
    )


def fit_oof(X, y, weeks):
    gkf = GroupKFold(n_splits=5)
    oof = np.full(len(y), np.nan)
    for tr, te in gkf.split(X, y, weeks):
        if y[tr].sum() == 0 or (1 - y[tr]).sum() == 0:
            continue
        m = _cb(); m.fit(X[tr], y[tr])
        oof[te] = m.predict_proba(X[te])[:, 1]
    return oof


def analyse(sub: pd.DataFrame, target: str, lift_col: str, max_n: int):
    lab = sub.groupby("session", group_keys=False).apply(lambda g: _runner_fizzle(g, target))
    if len(lab) > max_n:
        lab = lab.sample(max_n, random_state=0)
    lab = lab.reset_index(drop=True)
    if len(lab) < 300 or lab["runner"].nunique() < 2 or lab["week"].nunique() < 5:
        return None
    X = _clean(lab[FEATURES]); y = lab["runner"].to_numpy(int); weeks = lab["week"].to_numpy()
    oof = fit_oof(X, y, weeks)
    mask = np.isfinite(oof)
    auc = roc_auc_score(y[mask], oof[mask])
    # shuffled null
    rng = np.random.default_rng(0); null = []
    for _ in range(15):
        yp = y.copy()
        for w in np.unique(weeks):
            mm = weeks == w; yp[mm] = rng.permutation(yp[mm])
        o = fit_oof(X, yp, weeks); mk = np.isfinite(o)
        if mk.sum() > 20 and len(np.unique(yp[mk])) > 1:
            null.append(roc_auc_score(yp[mk], o[mk]))
    null_hi = np.nanpercentile(null, 95) if null else np.nan
    # top-decile lift on the realised outcome
    thr = np.nanpercentile(oof[mask], 90)
    top = mask & (oof >= thr)
    base_med = np.nanmedian(lab[lift_col].to_numpy()[mask])
    top_med = np.nanmedian(lab[lift_col].to_numpy()[top])
    # importances (full-fit) + native SHAP
    full = _cb(); full.fit(X, y)
    imp = pd.Series(full.get_feature_importance(), index=FEATURES)
    shap = full.get_feature_importance(Pool(X, y), type="ShapValues")
    mean_abs_shap = pd.Series(np.abs(shap[:, :-1]).mean(axis=0), index=FEATURES)
    # signed correlation of each feature with the outcome
    corr = {f: spearmanr(X[:, i], y).correlation for i, f in enumerate(FEATURES)}
    tbl = pd.DataFrame({"cb_imp": imp, "mean_shap": mean_abs_shap,
                        "spearman_y": pd.Series(corr)}).sort_values("mean_shap", ascending=False)
    return {"auc": auc, "null_hi": null_hi, "n": int(mask.sum()),
            "top_med": top_med, "base_med": base_med, "tbl": tbl, "X": X}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    ap.add_argument("--target", default="mfe120_atr")
    ap.add_argument("--lift-col", default="mfe120_atr")
    ap.add_argument("--max-n", type=int, default=45000)
    ap.add_argument("--corr-matrix", action="store_true", help="also dump strongest feature pairs")
    args = ap.parse_args()

    ev = pd.read_parquet(args.events)
    ev = ev[ev["break_ts"] < DEV_END_MS]  # DEV only; OOS frozen
    ev = add_features(ev)
    print(f"DEV events: {len(ev):,}  target={args.target}  features={len(FEATURES)}")

    if args.corr_matrix:
        Xall = _clean(ev[FEATURES])
        cm = pd.DataFrame(Xall, columns=FEATURES).corr(method="spearman")
        pairs = (cm.where(np.triu(np.ones(cm.shape), 1).astype(bool)).stack()
                 .sort_values(key=lambda s: s.abs(), ascending=False))
        print("\n=== strongest feature-feature |Spearman| pairs (redundancy) ===")
        print(pairs.head(15).round(2).to_string())

    for variant in ["sequential", "same_type"]:
        for tier in ["high", "mid", "low"]:
            sub = ev[(ev["variant"] == variant) & (ev["tier"] == tier)]
            if len(sub) < 400:
                continue
            r = analyse(sub, args.target, args.lift_col, args.max_n)
            if r is None:
                continue
            verdict = "SIGNAL" if (r["auc"] > r["null_hi"] and r["auc"] > 0.52) else "null"
            print(f"\n===== {variant} / {tier}  (n={r['n']:,}) =====")
            print(f"  CatBoost week-CV AUC={r['auc']:.3f}  null95={r['null_hi']:.3f} -> {verdict}"
                  f"   top-decile {args.lift_col} {r['top_med']:.2f} vs {r['base_med']:.2f}"
                  f" (x{r['top_med']/r['base_med']:.2f})")
            print(r["tbl"].head(12).to_string(float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
