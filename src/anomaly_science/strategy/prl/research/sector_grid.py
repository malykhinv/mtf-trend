"""Exhaustive regime-combination grid per setup: coin x BTC x ETH regime -> where is each optimal?

User: study ALL regime combos (coin sector in bull & BTC bull / coin bull & ETH side-or-bear / ...)
for each setup separately -- find the optimum. 3 anchors (coin-own, BTC, ETH; ETH = alt leader),
3 states each = 27 cells/setup. DISCIPLINE against overfit: report n and per-YEAR sign-stability
for every cell; emphasize robust MARGINAL effects over any small-n "best" cell. IS 2020-2025.
Run: python -m anomaly_science.strategy.prl.research.sector_grid --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h
from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag

COST = 6 / 1e4


def reg(v, thr=0.12):
    return "bull" if v > thr else ("bear" if v < -thr else "side")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None); btcs = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    eths = close["ETHUSDT"] if "ETHUSDT" in cols else btcs
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
    btc_r = (btcs / btcs.shift(60 * 24) - 1).to_numpy()
    eth_r = (eths / eths.shift(60 * 24) - 1).to_numpy()
    coin_r = (close / close.shift(60 * 24) - 1).to_numpy()
    C, Hh, L, A, HL = (x.to_numpy() for x in (close, high, low, atr, hi_lvl))
    pk = poke.to_numpy()

    def tags(k, si):
        return reg(coin_r[k, si]), reg(btc_r[k]), reg(eth_r[k]), idx[k].year

    sweep, bd, bo = [], [], []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + 64 >= len(idx) or not (datr[i, si] > 0) or not np.isfinite(coin_r[i, si]):
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
            sweep.append((bn, *tags(j, si)))
        SH, SL = zigzag(Hh[:, si], L[:, si], A[:, si], 4)
        below = C[:, si] < SL; fresh = below & ~np.concatenate([[False], below[:-1]])
        for e in np.where(fresh)[0]:
            if e + 48 >= len(idx) or not (C[e, si] > 0) or not np.isfinite(SH[e]) or not (A[e, si] > 0) or not np.isfinite(coin_r[e, si]):
                continue
            entry = C[e, si]; istop = SH[e]
            if istop <= entry:
                continue
            risk = (istop - entry) / entry
            if risk <= 0 or risk > 1.0:
                continue
            active = istop; xb, expx = e + 48, C[e + 48, si]
            for t in range(e + 1, e + 49):
                if np.isfinite(SH[t]):
                    active = min(active, SH[t])
                if C[t, si] > active:
                    xb, expx = t, C[t, si]; break
            bn = beta[e, si] * (btc[xb] / btc[e] - 1.0) - (expx / entry - 1.0) - COST
            bd.append((bn, *tags(e, si)))
        for i in np.where(brk[:, si])[0]:
            dj = i + 4
            if dj + 48 >= len(idx) or not (C[dj, si] > 0) or not (C[dj, si] >= HL[i, si]) or not np.isfinite(coin_r[dj, si]):
                continue
            raw = C[dj + 48, si] / C[dj, si] - 1.0 - COST
            bo.append((raw, *tags(dj, si)))

    def frame(rows):
        return pd.DataFrame(rows, columns=["pnl", "coin", "btc", "eth", "year"]).replace([np.inf, -np.inf], np.nan).dropna()

    for name, rows in [("BREAKOUT", bo), ("SWEEP", sweep), ("BREAKDOWN", bd)]:
        d = frame(rows)
        print(f"\n{'='*70}\n{name}: {len(d)} trades, base edge {d.pnl.mean()*100:+.3f}%")
        # marginal effect of each anchor
        print("  marginals (edge by each anchor's state):")
        for anc in ["coin", "btc", "eth"]:
            cells = "  ".join(f"{s}:{g.pnl.mean()*100:+.2f}%(n{len(g)})" for s, g in d.groupby(anc))
            print(f"    {anc:5s} {cells}")
        # full 3x3x3 grid: top combos by edge with n>=200 and year-stability
        g = d.groupby(["coin", "btc", "eth"])
        recs = []
        for key, gg in g:
            if len(gg) < 200:
                continue
            yrs = [gy.pnl.mean() for _, gy in gg.groupby("year") if len(gy) >= 20]
            stab = sum(1 for y in yrs if y > 0)
            recs.append((key, gg.pnl.mean(), len(gg), stab, len(yrs)))
        recs.sort(key=lambda r: -r[1])
        print("  TOP combos (coin/btc/eth) by edge, n>=200, +yrs/total:")
        for key, e, nn, stab, ny in recs[:6]:
            print(f"    {key[0]:4s}/{key[1]:4s}/{key[2]:4s}  edge={e*100:+.3f}%  n={nn:5d}  +yrs={stab}/{ny}")
        print("  WORST combos (avoid):")
        for key, e, nn, stab, ny in recs[-4:]:
            print(f"    {key[0]:4s}/{key[1]:4s}/{key[2]:4s}  edge={e*100:+.3f}%  n={nn:5d}  +yrs={stab}/{ny}")


if __name__ == "__main__":
    main()
