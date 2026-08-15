"""Bybit FUNDING (and OI) flow test -- one PRE-SPECIFIED economic hypothesis, no grid (avoid overfit).

Funding/OI are data NEVER used in the strategy selection -> an orthogonal robustness check. ONE
pre-specified hypothesis: high funding = crowded longs -> breakout is more of a trap -> sweep-fade
STRONGER and breakout-long WEAKER as funding rises. We condition each setup's edge on the funding
tercile at entry (and OI-change tercile if OI present). No parameter search. Run:
python -m anomaly_science.strategy.prl.research.bybit_flow_test --n 200
"""

from __future__ import annotations

import argparse
import glob
import os

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.bybit_validate import liquid, load
from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag

COST = 6 / 1e4
FUND = ".output/market/bybit/um_futures/funding_v1"
OID = ".output/market/bybit/um_futures/oi_1h"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=200); args = ap.parse_args()
    syms = liquid(args.n)
    panel = load(syms)
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None); btcs = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    br = btcs.pct_change(fill_method=None)
    beta = (ret.rolling(168, min_periods=48).cov(br).div(br.rolling(168, min_periods=48).var(), axis=0)).shift(1).clip(0, 3).fillna(1.0).to_numpy()
    btc = btcs.to_numpy()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    dh, dl, dc = high.resample("1D").max(), low.resample("1D").min(), close.resample("1D").last()
    dpc = dc.shift(1); dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
    datr = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
    hi_lvl = high.resample("1D").max().rolling(20, min_periods=10).max().shift(1).reindex(idx, method="ffill")
    above = close >= hi_lvl
    poke = (high >= hi_lvl) & (~above.shift(1).fillna(False)) & hi_lvl.notna()
    brk = (above & (~above.shift(1).fillna(False)) & hi_lvl.notna()).to_numpy()
    # funding matrix (causal: last known before entry)
    fu = {}
    for s in cols:
        f = f"{FUND}/{s}.parquet"
        if os.path.exists(f):
            d = pd.read_parquet(f); tt = pd.to_datetime(d["funding_time"], unit="ms", utc=True).dt.floor("h")
            fu[s] = d.groupby(tt)["funding_rate"].last()
    fund = (pd.DataFrame(fu).reindex(idx).ffill().shift(1).reindex(columns=cols).to_numpy() if fu else None)
    print(f"funding coverage: {int(np.isfinite(fund).any(axis=0).sum()) if fund is not None else 0}/{len(cols)} symbols")
    C, Hh, L, A, HL = (x.to_numpy() for x in (close, high, low, atr, hi_lvl))
    pk = poke.to_numpy()
    sw, bo = [], []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + 64 >= len(idx) or not (datr[i, si] > 0):
                continue
            lv = HL[i, si]; j = None
            for t in range(i, i + 5):
                if C[t, si] < lv:
                    j = t; break
            if j is None:
                continue
            ext = np.nanmax(Hh[i:j + 1, si]); entry = None
            for u in range(j + 1, j + 13):
                if Hh[u, si] >= lv:
                    entry = lv; start = u + 1; break
            if entry is None:
                continue
            dp = datr[j, si]; stop = ext + 0.25 * dp * entry; tp = entry * (1 - dp)
            expx, xb = C[min(start + 48, len(idx) - 1), si], min(start + 48, len(idx) - 1)
            for u in range(start, min(start + 48, len(idx))):
                if C[u, si] >= stop:
                    expx, xb = C[u, si], u; break
                if L[u, si] <= tp:
                    expx, xb = tp, u; break
            bn = beta[j, si] * (btc[xb] / btc[j] - 1.0) - (expx / entry - 1.0) - COST
            sw.append((bn, fund[j, si] if fund is not None else np.nan))
        for i in np.where(brk[:, si])[0]:
            dj = i + 4
            if dj + 48 >= len(idx) or not (C[dj, si] > 0) or not (C[dj, si] >= HL[i, si]):
                continue
            raw = C[dj + 48, si] / C[dj, si] - 1.0 - COST
            bo.append((raw, fund[dj, si] if fund is not None else np.nan))

    def by_funding(rows, label):
        d = pd.DataFrame(rows, columns=["pnl", "fund"]).replace([np.inf, -np.inf], np.nan).dropna()
        if len(d) < 300:
            print(f"  {label}: too few with funding (n={len(d)})"); return
        d["q"] = pd.qcut(d.fund.rank(method="first"), 3, labels=["low-fund", "mid", "high-fund"])
        cells = "  ".join(f"{q}: {g.pnl.mean()*100:+.3f}%(n{len(g)})" for q, g in d.groupby("q", observed=True))
        print(f"  {label:20s} {cells}")

    print("\n=== PRE-SPECIFIED: edge by funding tercile (hypothesis: high fund -> sweep UP, breakout DOWN) ===")
    by_funding(sw, "sweep-fade (bn)")
    by_funding(bo, "breakout (raw)")
    print("\n  hypothesis holds if: sweep high-fund > low-fund  AND  breakout high-fund < low-fund")


if __name__ == "__main__":
    main()
