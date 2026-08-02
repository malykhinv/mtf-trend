"""STAGE 5 -- CatBoost sharpening of the knife-catch base edge.

The flow-trigger dump buy-back has a positive, drift-controlled base rate (+0.19 R/trade,
week-stable). Can a model rank the entries so the confident decile beats the base rate,
above a shuffled null and week-stably? We predict the realized R (regression) from the
causal at-entry features, OOF via GroupKFold-by-week, then read top-decile meanR / posWk
and compare to a within-week label-shuffled null. DEV only; OOS untouched.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.model_selection import GroupKFold


def _oof(X, yv, wk, seed=0):
    o = np.full(len(yv), np.nan)
    for a, b in GroupKFold(5).split(X, yv, wk):
        m = CatBoostRegressor(depth=4, iterations=500, learning_rate=0.03, l2_leaf_reg=6,
                              loss_function="RMSE", verbose=False, random_seed=seed)
        m.fit(X[a], yv[a]); o[b] = m.predict(X[b])
    return o


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reversal", type=Path, default=Path(".output/results/knife_catch/dump_reversal.parquet"))
    ap.add_argument("--trigger", default="flow")
    args = ap.parse_args()
    t = pd.read_parquet(args.reversal)
    d = t[(t.trigger == args.trigger) & (t.entered == 1)].dropna(subset=["R"]).copy()
    feats = [c for c in d.columns if c.startswith("f_")]
    d = d.dropna(subset=feats)
    print(f"{args.trigger}: n={len(d)}  base meanR={d.R.mean():+.3f}  win={d.y.mean()*100:.1f}%  "
          f"posWk={ (d.groupby('week').R.mean()>0).mean()*100:.0f}%  weeks={d.week.nunique()}  feats={len(feats)}")

    X = d[feats].to_numpy(float); y = d.R.to_numpy(float); wk = d.week.to_numpy()
    med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    oof = _oof(X, y, wk)
    rng = np.random.default_rng(0)
    yn = d.groupby("week").R.transform(lambda s: rng.permutation(s.values)).to_numpy(float)
    oofn = _oof(X, yn, wk)
    s = d.assign(p=oof, pn=oofn)

    from scipy.stats import spearmanr
    print(f"\nrank corr(pred, R): real={spearmanr(oof, y).correlation:+.3f}  shuffled-null={spearmanr(oofn, yn).correlation:+.3f}")
    print(f"\n{'select':<10} {'n':>5} {'meanR':>7} {'win%':>6} {'posWk%':>7}   | shuffled meanR")
    for q, lbl in [(0.0, "ALL"), (0.5, "top50%"), (0.7, "top30%"), (0.8, "top20%"), (0.9, "top10%")]:
        sel = s[s.p >= s.p.quantile(q)]
        seln = s[s.pn >= s.pn.quantile(q)]
        wk2 = sel.groupby("week").R.mean()
        print(f"{lbl:<10} {len(sel):>5} {sel.R.mean():>+7.3f} {sel.y.mean()*100:>6.1f} {(wk2>0).mean()*100:>7.0f}   | {seln.R.mean():>+7.3f}")

    imp = pd.Series(CatBoostRegressor(depth=4, iterations=500, learning_rate=0.03, verbose=False, random_seed=0)
                    .fit(np.nan_to_num(d[feats].to_numpy(float)), y).get_feature_importance(),
                    index=feats).sort_values(ascending=False)
    print("\ntop features:", ", ".join(f"{k[2:]}={v:.1f}" for k, v in imp.head(12).items()))
    print(f"\n(base meanR {d.R.mean():+.3f}; a real edge => top decile meanR >> base AND >> shuffled, week-stable)")


if __name__ == "__main__":
    main()
