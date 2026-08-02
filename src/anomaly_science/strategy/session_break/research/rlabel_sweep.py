"""Fast TF x tier sweep of the MID-vs-ABOVE discrimination -- find the regime with
the strongest, most week-stable signal before spending the full audit + execution
on it. Week-grouped OOF CatBoost (lighter), AUC + top-decile MID lift + week
stability. No shuffled null here (the harness was cleared by reclaim_audit).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
DROP = {"symbol", "tf", "variant", "session", "break_ts", "week", "label", "trail_turnover"}
TIERS = {"all": (0, 1e18), "low": (0, 30e6), "mid": (30e6, 150e6), "high": (150e6, 1e18)}


def _cb():
    return CatBoostClassifier(depth=4, iterations=150, learning_rate=0.06, l2_leaf_reg=6.0,
                             loss_function="Logloss", random_seed=0, verbose=False, thread_count=6)


def evaluate(t: pd.DataFrame, feats, max_n):
    if len(t) > max_n:
        t = t.sample(max_n, random_state=0).reset_index(drop=True)
    X = t[feats].to_numpy(float)
    med = np.nanmedian(X, axis=0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    y = t.label.to_numpy(int); wk = t.week.to_numpy()
    if len(np.unique(wk)) < 5 or y.sum() < 30 or (1 - y).sum() < 30:
        return None
    oof = np.full(len(y), np.nan)
    for tr, te in GroupKFold(5).split(X, y, wk):
        if len(np.unique(y[tr])) < 2:
            continue
        m = _cb(); m.fit(X[tr], y[tr]); oof[te] = m.predict_proba(X[te])[:, 1]
    ok = np.isfinite(oof)
    auc = roc_auc_score(y[ok], oof[ok])
    thr = np.nanpercentile(oof[ok], 90)
    top = ok & (oof >= thr)
    base = y.mean(); top_mid = y[top].mean()
    dfm = pd.DataFrame({"w": wk[ok], "y": y[ok], "s": oof[ok]})
    beats = [g[g.s >= g.s.quantile(0.8)].y.mean() > g.y.mean()
             for _, g in dfm.groupby("w") if len(g) >= 20 and len(g[g.s >= g.s.quantile(0.8)]) >= 3]
    return dict(n=len(t), base=base, auc=auc, top_mid=top_mid, lift=top_mid - base,
                stab=float(np.mean(beats)) if beats else np.nan)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tfs", default="60,30,15")
    ap.add_argument("--max-n", type=int, default=60000)
    args = ap.parse_args()
    print(f"{'tf':>4} {'tier':>5} {'n':>7} {'base':>5} {'AUC':>6} {'top_MID':>7} {'lift':>6} {'week_stab':>9}")
    for tf in [int(x) for x in args.tfs.split(",")]:
        p = Path(f".output/results/session_break/rlabel_tf{tf}.parquet")
        if not p.exists():
            print(f"{tf:>4}  (missing)"); continue
        raw = pd.read_parquet(p)
        raw = raw[(raw.break_ts < DEV_END) & (raw.label >= 0)]
        feats = [c for c in raw.columns if c not in DROP]
        for tier, (lo, hi) in TIERS.items():
            sub = raw[(raw.trail_turnover >= lo) & (raw.trail_turnover < hi)]
            r = evaluate(sub, feats, args.max_n)
            if r is None:
                continue
            print(f"{tf:>4} {tier:>5} {r['n']:>7,} {r['base']:>5.2f} {r['auc']:>6.3f} "
                  f"{r['top_mid']:>7.3f} {r['lift']:>+6.3f} {r['stab']:>9.0%}")


if __name__ == "__main__":
    main()
