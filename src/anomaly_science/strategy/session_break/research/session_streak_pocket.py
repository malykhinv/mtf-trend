"""Is the OVERLAP/US short pocket REAL or just the best of 15 slices? For each
session we compute the OOF AUC against a SESSION-SPECIFIC within-week shuffled null
(averaged over several shuffles for a noise band), plus the top-decile weekly
win-rate with a block-bootstrap CI. Then train a model specialised on the promising
sessions to see whether focusing helps. Still strictly in-sample.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from anomaly_science.strategy.session_break.research.session_streak import FEATS
from anomaly_science.strategy.session_break.research.sessions import BLOCK_BY_SEQ

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
COST_BP = 8.0


def oof(d, shuffle=False, seed=0):
    X = d[FEATS].to_numpy(float); y = d.cont.to_numpy(int); wk = d.week.to_numpy()
    if shuffle:
        rng = np.random.default_rng(seed)
        y = d.groupby("week").cont.transform(lambda s: rng.permutation(s.values)).to_numpy(int)
    med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    o = np.full(len(y), np.nan)
    for tr, te in GroupKFold(5).split(X, y, wk):
        if len(np.unique(y[tr])) < 2:
            continue
        m = CatBoostClassifier(depth=5, iterations=300, learning_rate=0.03, l2_leaf_reg=6,
                               verbose=False, random_seed=seed)
        m.fit(X[tr], y[tr]); o[te] = m.predict_proba(X[te])[:, 1]
    return o, y


def topdec(d):
    d = d[np.isfinite(d.p)]
    thr = np.nanpercentile(d.p, 90); td = d[d.p >= thr]
    wk = td.groupby("week").trade_ret.mean()
    return td.trade_ret.mean() * 1e4 - COST_BP, (wk > 0).mean(), len(wk)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/streak.parquet"))
    ap.add_argument("--nshuffle", type=int, default=3)
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    t = t[t.start_ts < DEV_END].copy()
    t["session"] = t.seq.map(lambda s: BLOCK_BY_SEQ[int(s)].name)

    for direction, dname in ((-1, "SHORT"), (+1, "LONG")):
        d0 = t[t.direction == direction]
        print(f"\n================ {dname} ================")
        print(f"{'session':<9}{'n':>7}{'real_AUC':>9}{'null_AUC(band)':>18}{'edge':>7}{'topDec_net':>11}{'topDec_wk':>10}")
        for sess in ["ASIA", "EU", "OVERLAP", "US", "LATE"]:
            d = d0[d0.session == sess].copy()
            if len(d) < 2000:
                continue
            p, y = oof(d)
            d = d.assign(p=p)
            mk = np.isfinite(d.p)
            real = roc_auc_score(d.cont[mk], d.p[mk])
            nulls = []
            for s in range(args.nshuffle):
                pn, yn = oof(d, shuffle=True, seed=100 + s)
                m2 = np.isfinite(pn)
                nulls.append(roc_auc_score(yn[m2], pn[m2]))
            nmean, nstd = float(np.mean(nulls)), float(np.std(nulls))
            edge = real - nmean
            net, wkwin, nwk = topdec(d)
            print(f"{sess:<9}{len(d):>7}{real:>9.3f}   {nmean:>6.3f}±{nstd:.3f}     {edge:>+6.3f}{net:>+10.1f}b{wkwin:>9.2f}")

        # specialised model on OVERLAP+US shorts (the promising pocket)
        if direction < 0:
            d = d0[d0.session.isin(["OVERLAP", "US"])].copy()
            p, y = oof(d); d = d.assign(p=p)
            mk = np.isfinite(d.p)
            real = roc_auc_score(d.cont[mk], d.p[mk])
            nl = []
            for s in range(args.nshuffle):
                pn, yn = oof(d, shuffle=True, seed=200 + s); m2 = np.isfinite(pn); nl.append(roc_auc_score(yn[m2], pn[m2]))
            net, wkwin, nwk = topdec(d)
            print(f"\n  SPECIALISED OVERLAP+US short: n={len(d):,} real_AUC={real:.3f} null={np.mean(nl):.3f} "
                  f"edge={real-np.mean(nl):+.3f} topDec_net={net:+.1f}b topDec_wk={wkwin:.2f} over {nwk} weeks")
            # top feature importances for this pocket
            X = d[FEATS].to_numpy(float); yv = d.cont.to_numpy(int)
            med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
            imp = pd.Series(CatBoostClassifier(depth=5, iterations=300, learning_rate=0.03, verbose=False, random_seed=0)
                            .fit(X, yv).get_feature_importance(), index=FEATS).sort_values(ascending=False)
            print("  pocket top features:", ", ".join(f"{k}={v:.1f}" for k, v in imp.head(10).items()))


if __name__ == "__main__":
    main()
