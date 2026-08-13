"""Full win/lose picture on the daily book + can we cut losers? (user)

Three honest questions on the daily book's name-trades (top/bottom-decile, 15d hold,
market-relative outcome), for the long and short legs separately:

  1. WHAT separates win from lose -- standardized feature gap, WITH per-year sign
     stability (a separator that flips sign across years is a mirage).
  2. HOW separable are losers -- a causal walk-forward win-classifier's OOF AUC.
  3. Does CUTTING predicted losers help -- per-year mean/weekly of the filtered book,
     with a SHUFFLE control (permute win labels, refit; a real filter must beat this).

IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.loser_filter
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.discriminate import build_trades

OUT = Path(".output/results/prl_coarse")


def _oof_winprob(g, feats, seed=0):
    """Walk-forward OOF P(win) for one leg's trades; returns a Series on g.index."""
    from catboost import CatBoostClassifier
    dn = pd.to_datetime(g["date"]).dt.tz_localize(None)
    oof = pd.Series(np.nan, index=g.index)
    for tr_d, te_d in time_folds(dn.values, 5, embargo=17):
        tri = g.index[dn.isin(tr_d)]; tei = g.index[dn.isin(te_d)]
        if len(tri) < 100 or len(tei) < 1 or g.loc[tri, "win"].nunique() < 2:
            continue
        cb = CatBoostClassifier(iterations=300, depth=4, learning_rate=0.03,
                                l2_leaf_reg=8.0, random_seed=seed, verbose=False)
        cb.fit(g.loc[tri, feats], g.loc[tri, "win"])
        oof.loc[tei] = cb.predict_proba(g.loc[tei, feats])[:, 1]
    return oof


def _auc(y, s):
    from sklearn.metrics import roc_auc_score
    m = np.isfinite(s)
    return roc_auc_score(y[m], s[m]) if m.sum() > 20 and len(np.unique(y[m])) == 2 else np.nan


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tr, feats = build_trades()
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    tr["winprob"] = np.nan
    print(f"trades: {len(tr)}  longs={int((tr.side>0).sum())}  shorts={int((tr.side<0).sum())}\n")

    for side, nm in ((+1, "LONG"), (-1, "SHORT")):
        g = tr[tr.side == side]
        print(f"=== {nm}: win-rate={g.win.mean():.3f} ===")
        # 1. separation with per-year stability
        rows = []
        for f in feats:
            gap = (g.loc[g.win == 1, f].mean() - g.loc[g.win == 0, f].mean()) / (g[f].std() + 1e-9)
            yr_signs = []
            for y, gy in g.groupby("year"):
                if gy.win.nunique() == 2:
                    yr_signs.append(np.sign(gy.loc[gy.win == 1, f].mean() - gy.loc[gy.win == 0, f].mean()))
            stable = len(yr_signs) > 1 and all(s == yr_signs[0] for s in yr_signs)
            rows.append((f, gap, stable))
        rows.sort(key=lambda x: -abs(x[1]))
        print("  top separators (win-lose, std units; * = sign-stable across years):")
        for f, gap, stable in rows[:8]:
            print(f"    {f:20s} {gap:+.3f} {'*' if stable else ' '}")

        # 2. how separable -- OOF AUC
        wp = _oof_winprob(g, feats)
        auc = _auc(g["win"].to_numpy(), wp.to_numpy())
        print(f"  walk-forward OOF win-classifier AUC = {auc:.3f}  (0.5 = losers not separable)")
        tr.loc[g.index, "winprob"] = wp
        print()

    # 3. filter test: keep top-half win-prob per leg, per-rebalance equal-weight L/S
    def book(df, prob_col):
        keep = df.dropna(subset=[prob_col]).copy()
        keep["signed"] = keep["rel"] * keep["side"]   # long:+rel, short:-rel (dollar-neutral)
        keep["k"] = keep.groupby(["date", "side"])[prob_col].transform(lambda s: s >= s.median())
        base = keep.groupby("date")["signed"].mean()
        filt = keep[keep["k"]].groupby("date")["signed"].mean()
        def stats(s):
            s = s.dropna(); s.index = pd.to_datetime(s.index)
            cagr = (1 + s).prod() ** (17.0 / len(s)) - 1
            yr = {y: (1 + v).prod() - 1 for y, v in s.groupby(s.index.year)}
            return cagr, float((s > 0).mean()), yr
        return stats(base), stats(filt)

    print("=== FILTER: cut bottom-half win-prob per leg (per-rebalance equal-weight L/S) ===")
    (bc, bp, byr), (fc, fp, fyr) = book(tr, "winprob")
    print(f"  baseline (all)   CAGR~{bc*100:+.0f}%  +rebs={bp*100:.0f}%  " + " ".join(f"{y}:{v*100:+.0f}%" for y, v in byr.items()))
    print(f"  filtered (top50) CAGR~{fc*100:+.0f}%  +rebs={fp*100:.0f}%  " + " ".join(f"{y}:{v*100:+.0f}%" for y, v in fyr.items()))

    # shuffle control: permute win labels within leg, refit, filter -> must NOT help
    print("\n  SHUFFLE control (win labels permuted within leg):")
    tr2 = tr.copy()
    rng = np.random.default_rng(1)
    for side in (+1, -1):
        idx = tr2.index[tr2.side == side]
        tr2.loc[idx, "win"] = rng.permutation(tr2.loc[idx, "win"].values)
    tr2["winprob"] = np.nan
    for side in (+1, -1):
        g = tr2[tr2.side == side]
        tr2.loc[g.index, "winprob"] = _oof_winprob(g, feats)
    (_, _, _), (sc, sp, syr) = book(tr2, "winprob")
    print(f"  filtered on SHUFFLED prob  CAGR~{sc*100:+.0f}%  +rebs={sp*100:.0f}%  "
          + " ".join(f"{y}:{v*100:+.0f}%" for y, v in syr.items()))
    print("  (if filtered-real >> baseline AND >> filtered-shuffled -> a real loser cut)")


if __name__ == "__main__":
    main()
