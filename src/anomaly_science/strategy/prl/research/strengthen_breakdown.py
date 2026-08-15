"""Strengthen setup #2 (breakdown-short continuation): regime home + continuation selector.

Full 2020-25, beta-neutral, structural stop (zigzag N=4) + trail. Levers: (1) per-BTC-regime edge
-- is it a BEAR/downtrend specialist? (2) a walk-forward selector for which breakdowns CONTINUE vs
bounce (prior downtrend strength, volume on break, range position, extension, ATR, market regime).
Report edge by selection decile and by regime, + regime-gate (only downtrend) vs all. IS only.
Run: python -m anomaly_science.strategy.prl.research.strengthen_breakdown --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h
from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag

MH = 48
COST = 6 / 1e4


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv = (px(c) for c in ("close", "high", "low", "quote_volume"))
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None); btcs = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    br = btcs.pct_change(fill_method=None)
    beta = (ret.rolling(168, min_periods=48).cov(br).div(br.rolling(168, min_periods=48).var(), axis=0)).shift(1).clip(0, 3).fillna(1.0).to_numpy()
    btc = btcs.to_numpy()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    r60 = (btcs / btcs.shift(60 * 24) - 1).to_numpy()
    breadth = (close >= close.rolling(480, min_periods=100).max().shift(1)).mean(axis=1).to_numpy()
    btcvol = btcs.pct_change().rolling(30 * 24, min_periods=100).std().to_numpy()
    vs = (qv / qv.rolling(168, min_periods=48).mean())
    ema = (close / close.ewm(span=168, min_periods=84).mean() - 1)
    m24 = close / close.shift(24) - 1; m72 = close / close.shift(72) - 1
    C, Hh, L, A, VS, EMA, M24, M72 = (x.to_numpy() for x in (close, high, low, atr, vs, ema, m24, m72))
    rows = []
    for si in range(len(cols)):
        SH, SL = zigzag(Hh[:, si], L[:, si], A[:, si], 4)
        below = C[:, si] < SL
        fresh = below & ~np.concatenate([[False], below[:-1]])
        for e in np.where(fresh)[0]:
            if e + MH >= len(idx) or not (C[e, si] > 0) or not np.isfinite(SH[e]) or not (A[e, si] > 0) or not (A[e, si] > 0):
                continue
            entry = C[e, si]; istop = SH[e]
            if istop <= entry:
                continue
            risk = (istop - entry) / entry
            if risk <= 0 or risk > 1.0:
                continue
            active = istop; xb, expx = e + MH, C[e + MH, si]
            for t in range(e + 1, e + MH + 1):
                if np.isfinite(SH[t]):
                    active = min(active, SH[t])
                if C[t, si] > active:
                    xb, expx = t, C[t, si]; break
            bn = beta[e, si] * (btc[xb] / btc[e] - 1.0) - (expx / entry - 1.0) - COST
            reg = "bull" if r60[e] > 0.12 else ("bear" if r60[e] < -0.12 else "side")
            rows.append(dict(date=idx[e], bn=bn, win=int(bn > 0), risk=risk,
                             mom24=M24[e, si], mom72=M72[e, si], vsurge=VS[e, si], ema=EMA[e, si],
                             atrp=A[e, si] / entry, breadth=breadth[e], r60=r60[e], btcvol=btcvol[e], regime=reg))
    tr = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    feats = ["mom24", "mom72", "vsurge", "ema", "atrp", "breadth", "r60", "btcvol"]
    print(f"\nbreakdowns: {len(tr)}  base beta-neutral edge {tr.bn.mean()*100:+.3f}%")

    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score
    dn = pd.to_datetime(tr["date"]).dt.tz_localize(None)
    oof = pd.Series(np.nan, index=tr.index)
    for trd, ted in time_folds(dn.values, 6, embargo=2):
        tri = tr.index[dn.isin(trd)]; tei = tr.index[dn.isin(ted)]
        if len(tri) < 300 or tr.loc[tri, "win"].nunique() < 2:
            continue
        cb = CatBoostClassifier(iterations=350, depth=5, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
        cb.fit(tr.loc[tri, feats], tr.loc[tri, "win"]); oof.loc[tei] = cb.predict_proba(tr.loc[tei, feats])[:, 1]
    tr["score"] = oof; mm = oof.notna()
    print(f"continuation AUC = {roc_auc_score(tr.loc[mm,'win'], oof[mm]):.3f}")

    print("\n=== edge by selection ===")
    g = tr[mm]
    for frac in (1.0, 0.5, 0.25, 0.1):
        sel = g[g.score >= g.score.quantile(1 - frac)]
        print(f"  top {frac:.0%}  n={len(sel):6d}  edge={sel.bn.mean()*100:+.3f}%")
    print("\n=== base edge by BTC regime (home regime?) ===")
    for reg, gg in tr.groupby("regime"):
        ys = " ".join(f"{y}:{g2.bn.mean()*100:+.2f}" for y, g2 in gg.groupby("year"))
        print(f"  {reg:5s} n={len(gg):6d}  edge={gg.bn.mean()*100:+.3f}%  win={gg.win.mean():.0%}  [{ys}]")
    print("\n=== regime-gate: downtrend-only (r60<0) vs all ===")
    dn_only = tr[tr.r60 < 0]
    print(f"  all            n={len(tr):6d}  edge={tr.bn.mean()*100:+.3f}%")
    print(f"  r60<0 (down)   n={len(dn_only):6d}  edge={dn_only.bn.mean()*100:+.3f}%")
    print(f"  r60<-0.05      n={len(tr[tr.r60<-0.05]):6d}  edge={tr[tr.r60<-0.05].bn.mean()*100:+.3f}%")


if __name__ == "__main__":
    main()
