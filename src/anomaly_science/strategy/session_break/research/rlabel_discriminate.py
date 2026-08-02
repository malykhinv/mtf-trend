"""CatBoost discrimination: MID (revert to range mid) vs ABOVE (consolidate back
above the high), on the labelled unconfident-poke events. Finds week-STABLE
patterns, not just an in-sample fit.

Reports: week-grouped OOF AUC vs within-week shuffled null; CatBoost importance +
mean|SHAP|; each feature's signed Spearman vs the MID label; and the anti-lottery
check -- across weeks, does the top-decile-by-score have a higher MID rate than the
week's base rate, and in how many weeks.
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

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
DROP = {"symbol", "tf", "variant", "session", "break_ts", "week", "label", "trail_turnover"}


def _cb():
    return CatBoostClassifier(depth=4, iterations=350, learning_rate=0.04, l2_leaf_reg=6.0,
                             loss_function="Logloss", random_seed=0, verbose=False)


def _clean(df, feats):
    X = df[feats].to_numpy(float)
    med = np.nanmedian(X, axis=0); ix = np.where(~np.isfinite(X))
    X[ix] = np.take(med, ix[1])
    lo = np.nanpercentile(X, 1, axis=0); hi = np.nanpercentile(X, 99, axis=0)
    return np.clip(X, lo, hi)


def oof(X, y, wk, kind="cb"):
    o = np.full(len(y), np.nan)
    for tr, te in GroupKFold(5).split(X, y, wk):
        if len(np.unique(y[tr])) < 2:
            continue
        m = _cb(); m.fit(X[tr], y[tr]); o[te] = m.predict_proba(X[te])[:, 1]
    return o


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, required=True)
    ap.add_argument("--tier", default="all", choices=["all", "low", "mid", "high"])
    ap.add_argument("--drop", default="", help="comma-separated features to exclude (mechanical-leak check)")
    ap.add_argument("--max-n", type=int, default=0, help="subsample for speed (0=all)")
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    t = t[(t.break_ts < DEV_END) & (t.label >= 0)].copy()
    if args.tier != "all":
        cut = {"low": (0, 30e6), "mid": (30e6, 150e6), "high": (150e6, 1e18)}[args.tier]
        t = t[(t.trail_turnover >= cut[0]) & (t.trail_turnover < cut[1])]
    if args.max_n and len(t) > args.max_n:
        t = t.sample(args.max_n, random_state=0)
    drop = set(DROP) | {d.strip() for d in args.drop.split(",") if d.strip()}
    feats = [c for c in t.columns if c not in drop]
    if args.drop:
        print(f"[drop] excluded: {args.drop}")
    y = t.label.to_numpy(int); wk = t.week.to_numpy(); X = _clean(t, feats)
    base = y.mean()
    print(f"{args.events.name} tier={args.tier}: n={len(t):,}  MID base={base:.3f}  feats={len(feats)}  weeks={t.week.nunique()}")

    o = oof(X, y, wk); m = np.isfinite(o)
    auc = roc_auc_score(y[m], o[m])
    # within-week shuffled null
    rng = np.random.default_rng(0); null = []
    for _ in range(12):
        yp = y.copy()
        for w in np.unique(wk):
            mm = wk == w; yp[mm] = rng.permutation(yp[mm])
        op = oof(X, yp, wk); mk = np.isfinite(op)
        if len(np.unique(yp[mk])) > 1:
            null.append(roc_auc_score(yp[mk], op[mk]))
    nhi = np.nanpercentile(null, 95) if null else np.nan
    print(f"  week-CV AUC={auc:.3f}  shuffled-null95={nhi:.3f}  -> {'SIGNAL' if auc>nhi and auc>0.55 else 'weak/null'}")

    # top-decile MID rate + week stability (anti-lottery)
    thr = np.nanpercentile(o[m], 90)
    top = m & (o >= thr)
    print(f"  top-decile MID rate={y[top].mean():.3f} vs base {base:.3f}  (lift +{y[top].mean()-base:.3f})")
    dfm = pd.DataFrame({"w": wk[m], "y": y[m], "s": o[m]})
    beats = []
    for w, g in dfm.groupby("w"):
        if len(g) < 20:
            continue
        gt = g[g.s >= g.s.quantile(0.8)]
        if len(gt) >= 3:
            beats.append(gt.y.mean() > g.y.mean())
    print(f"  weeks where top-quintile MID-rate beats week base: {np.mean(beats):.0%} of {len(beats)} weeks")

    # importance + SHAP + signed correlation
    full = _cb(); full.fit(X, y)
    imp = pd.Series(full.get_feature_importance(), index=feats)
    shap = np.abs(full.get_feature_importance(Pool(X, y), type="ShapValues")[:, :-1]).mean(0)
    corr = {f: spearmanr(X[:, i], y).correlation for i, f in enumerate(feats)}
    tbl = pd.DataFrame({"cb_imp": imp, "shap": pd.Series(shap, index=feats),
                        "spearman_MID": pd.Series(corr)}).sort_values("shap", ascending=False)
    print(tbl.head(16).to_string(float_format=lambda x: f"{x:+.3f}"))


if __name__ == "__main__":
    main()
