"""Strengthen setup #1 (sweep-fade / закол): rich reverter selector + regime split, full 2020-25.

The earlier reverter selector was weak (AUC 0.547 on 3 years). With 6 years now and a richer causal
feature set (wick overshoot, reclaim depth, fail speed, sweep volume/taker, coin momentum, extension,
ATR, funding, and the MARKET regime at the sweep), can we select the reverting sweeps and lift the
beta-neutral per-trade edge above the ~+0.37% base -- net of cost, and stable across regimes? Walk-
forward OOF, edge by selection decile, per BTC-regime, shuffle control. IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.strengthen_sweep --n 150
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

FUND_DIR = ".output/market/binance_vision/um_futures/funding_v1"
LVL, KF, RW, MH = 20, 4, 12, 48
COST = 6 / 1e4


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv, tbq = (px(c) for c in ("close", "high", "low", "quote_volume", "taker_buy_quote_volume"))
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None); btcs = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    br = btcs.pct_change(fill_method=None)
    beta = (ret.rolling(168, min_periods=48).cov(br).div(br.rolling(168, min_periods=48).var(), axis=0)).shift(1).clip(0, 3).fillna(1.0).to_numpy()
    btc = btcs.to_numpy()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    hi_lvl = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    dh, dl, dc = high.resample("1D").max(), low.resample("1D").min(), close.resample("1D").last()
    dpc = dc.shift(1); dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
    datr = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
    above = close >= hi_lvl
    poke = (high >= hi_lvl) & (~above.shift(1).fillna(False)) & hi_lvl.notna()
    # market regime (causal): BTC 60d trend + breadth + vol
    r60 = (btcs / btcs.shift(60 * 24) - 1).to_numpy()
    breadth = above.mean(axis=1).to_numpy()
    btcvol = btcs.pct_change().rolling(30 * 24, min_periods=100).std().to_numpy()
    vs = (qv / qv.rolling(168, min_periods=48).mean())
    taker = (2 * tbq - qv) / (qv + 1e-9)
    ema = (close / close.ewm(span=168, min_periods=84).mean() - 1)
    m24 = close / close.shift(24) - 1
    fu = {}
    for s in cols:
        f = f"{FUND_DIR}/{s}.parquet"
        if os.path.exists(f):
            d = pd.read_parquet(f); tt = pd.to_datetime(d["funding_time"], unit="ms", utc=True).dt.floor("h")
            fu[s] = d.groupby(tt)["funding_rate"].last()
    fund = (pd.DataFrame(fu).reindex(idx).ffill().reindex(columns=cols).to_numpy() if fu else np.zeros((len(idx), len(cols))))
    C, Hh, L, A, HL, VS, TK, EMA, M24 = (x.to_numpy() for x in (close, high, low, atr, hi_lvl, vs, taker, ema, m24))
    pk = poke.to_numpy(); rows = []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + KF + RW + MH >= len(idx) or not (datr[i, si] > 0):
                continue
            lv = HL[i, si]; j = None
            for t in range(i, i + KF + 1):
                if C[t, si] < lv:
                    j = t; break
            if j is None:
                continue
            ext = np.nanmax(Hh[i:j + 1, si]); entry = None
            for u in range(j + 1, j + 1 + RW):
                if Hh[u, si] >= lv:
                    entry = lv; start = u + 1; break
            if entry is None:
                continue
            dp = datr[j, si]; stop = ext + 0.25 * dp * entry; tp = entry * (1 - dp)
            expx, xb = C[min(start + MH, len(idx) - 1), si], min(start + MH, len(idx) - 1)
            for u in range(start, min(start + MH, len(idx))):
                if C[u, si] >= stop:
                    expx, xb = C[u, si], u; break
                if L[u, si] <= tp:
                    expx, xb = tp, u; break
            bn = beta[j, si] * (btc[xb] / btc[j] - 1.0) - (expx / entry - 1.0) - COST
            reg = "bull" if r60[j] > 0.12 else ("bear" if r60[j] < -0.12 else "side")
            rows.append(dict(date=idx[start - 1], bn=bn, win=int(bn > 0),
                             wick=(ext - lv) / (A[i, si] + 1e-9), reclaim=(lv - C[j, si]) / (A[i, si] + 1e-9),
                             failspd=j - i, vsurge=VS[i, si], taker=TK[i, si], mom24=M24[i, si],
                             ema=EMA[i, si], atrp=A[i, si] / entry, fund=fund[j, si],
                             breadth=breadth[j], r60=r60[j], btcvol=btcvol[j], regime=reg))
    tr = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    feats = ["wick", "reclaim", "failspd", "vsurge", "taker", "mom24", "ema", "atrp", "fund", "breadth", "r60", "btcvol"]
    print(f"\nsweeps: {len(tr)}  base beta-neutral edge {tr.bn.mean()*100:+.3f}%")

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
    tr["score"] = oof
    mm = oof.notna()
    print(f"reverter AUC = {roc_auc_score(tr.loc[mm,'win'], oof[mm]):.3f} (was 0.547 on 3yr)")

    print("\n=== beta-neutral edge by selection (net of cost already in bn) ===")
    g = tr[mm]
    for frac in (1.0, 0.5, 0.25, 0.1):
        sel = g[g.score >= g.score.quantile(1 - frac)]
        ys = " ".join(f"{y}:{gg.bn.mean()*100:+.2f}" for y, gg in sel.groupby("year"))
        print(f"  top {frac:.0%}  n={len(sel):5d}  edge={sel.bn.mean()*100:+.3f}%  [{ys}]")

    print("\n=== base edge by BTC regime (where does the fade win/lose?) ===")
    for reg, gg in tr.groupby("regime"):
        print(f"  {reg:5s} n={len(gg):5d}  edge={gg.bn.mean()*100:+.3f}%  win={gg.win.mean():.0%}")
    # regime x selection: does selecting help most where base is weak?
    print("\n=== top-25% selected, by regime ===")
    for reg, gg in g[g.score >= g.score.quantile(0.75)].groupby("regime"):
        print(f"  {reg:5s} n={len(gg):5d}  edge={gg.bn.mean()*100:+.3f}%")


if __name__ == "__main__":
    main()
