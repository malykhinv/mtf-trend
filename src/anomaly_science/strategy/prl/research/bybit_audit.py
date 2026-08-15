"""HARSHEST look-ahead / self-deception audit of the Bybit cross-venue result.

Key self-deception risk: Bybit majors are the SAME assets as Binance (arbitrage) -> reproduction is
expected, ruling out DATA artifacts but NOT a fragile crypto edge. So the decisive test is the
BYBIT-ONLY universe (coins not listed on Binance = genuinely independent). Plus: raw-vs-hedge
decomposition (is it the fade or the BTC-beta?), +1-bar execution delay, 10bps cost, and a random-
up-pop PLACEBO (does the payoff structure alone earn it?). Reports each setup on shared vs Bybit-only.
Run: python -m anomaly_science.strategy.prl.research.bybit_audit --n 200
"""

from __future__ import annotations

import argparse
import glob
import os

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.bybit_validate import liquid, load, reg
from anomaly_science.strategy.prl.research.breakdown_short_v2 import zigzag

COST = 6 / 1e4
BINANCE_H1 = ".output/market/binance_vision/um_futures/klines_1h"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=200); args = ap.parse_args()
    binance_syms = {os.path.basename(f)[:-8] for f in glob.glob(f"{BINANCE_H1}/*USDT.parquet")}
    syms = liquid(args.n)
    only = [s for s in syms if s not in binance_syms]
    print(f"bybit liquid {len(syms)}; NOT on binance (independent): {len(only)}  e.g. {only[:8]}")
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
    btc_r = (btcs / btcs.shift(60 * 24) - 1).to_numpy(); coin_r = (close / close.shift(60 * 24) - 1).to_numpy()
    C, Hh, L, A, HL = (x.to_numpy() for x in (close, high, low, atr, hi_lvl))
    pk = poke.to_numpy(); onlyset = set(only)

    def run(delay=0, cost=COST, placebo=False):
        sw, bd, bo = [], [], []
        rng = np.random.default_rng(1)
        for si in range(len(cols)):
            indep = cols[si] in onlyset
            # sweep
            for i in np.where(pk[:, si])[0]:
                if i + 64 + delay >= len(idx) or not (datr[i, si] > 0) or not np.isfinite(coin_r[i, si]):
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
                        entry = lv; start = u + 1 + delay; break
                if entry is None or start >= len(idx):
                    continue
                if placebo:                                   # random up-pop entry, same mechanic
                    t = int(rng.integers(200, len(idx) - 64));
                    if not (datr[t, si] > 0 and C[t, si] > 0 and Hh[t, si] > Hh[t - 3, si]):
                        continue
                    j = t; entry = C[t, si]; start = t + 1; ext = Hh[t, si]
                dp = datr[j, si]; stop = ext + 0.25 * dp * entry; tp = entry * (1 - dp)
                expx, xb = C[min(start + 48, len(idx) - 1), si], min(start + 48, len(idx) - 1)
                for u in range(start, min(start + 48, len(idx))):
                    if C[u, si] >= stop:
                        expx, xb = C[u, si], u; break
                    if L[u, si] <= tp:
                        expx, xb = tp, u; break
                raw = (entry - expx) / entry; hedge = btc[xb] / btc[j] - 1.0
                sw.append((beta[j, si] * hedge - (expx / entry - 1.0) - cost, raw - cost, indep))
            # breakdown (skip in placebo)
            if not placebo:
                SH, SL = zigzag(Hh[:, si], L[:, si], A[:, si], 4)
                below = C[:, si] < SL; fresh = below & ~np.concatenate([[False], below[:-1]])
                for e in np.where(fresh)[0]:
                    if e + 48 + delay >= len(idx) or not (C[e, si] > 0) or not np.isfinite(SH[e]) or not (A[e, si] > 0):
                        continue
                    entry = C[e, si]; istop = SH[e]
                    if istop <= entry:
                        continue
                    risk = (istop - entry) / entry
                    if risk <= 0 or risk > 1.0:
                        continue
                    st = e + 1 + delay; active = istop; xb, expx = min(st + 47, len(idx) - 1), C[min(st + 47, len(idx) - 1), si]
                    for t in range(st, min(st + 48, len(idx))):
                        if np.isfinite(SH[t]):
                            active = min(active, SH[t])
                        if C[t, si] > active:
                            xb, expx = t, C[t, si]; break
                    bn = beta[e, si] * (btc[xb] / btc[e] - 1.0) - (expx / entry - 1.0) - cost
                    if reg(coin_r[e, si]) == "bear" and reg(btc_r[e]) == "bear":
                        bd.append((bn, indep))
                for i in np.where(brk[:, si])[0]:
                    dj = i + 4 + delay
                    if dj + 48 >= len(idx) or not (C[dj, si] > 0) or not (C[dj, si] >= HL[i, si]) or not np.isfinite(coin_r[dj, si]):
                        continue
                    if reg(btc_r[dj]) == "bull":
                        bo.append((C[dj + 48, si] / C[dj, si] - 1.0 - cost, indep))
        return sw, bd, bo

    def summ(rows, label, idxcol=0):
        r = np.array([x[idxcol] for x in rows]); r = r[np.isfinite(r)]
        ind = np.array([x[-1] for x in rows]); ri = np.array([x[idxcol] for x in rows]); fin = np.isfinite(ri)
        rid = ri[fin & ind]; ral = ri[fin]
        print(f"  {label:22s} ALL n={len(ral):6d} edge={ral.mean()*100:+.3f}%   BYBIT-ONLY n={len(rid):5d} edge={rid.mean()*100 if len(rid) else 0:+.3f}%")

    print("\n=== BASE (6bps): shared+all vs BYBIT-ONLY (independent coins) ===")
    sw, bd, bo = run()
    summ(sw, "sweep (beta-neutral)", 0)
    summ(sw, "sweep (RAW short)", 1)
    summ(bd, "breakdown(coin+btc bear)", 0)
    summ(bo, "breakout(bull, raw)", 0)

    print("\n=== +1-bar delay ===")
    sw1, bd1, bo1 = run(delay=1)
    summ(sw1, "sweep bn", 0); summ(bd1, "breakdown", 0); summ(bo1, "breakout", 0)
    print("\n=== cost 10bps ===")
    sw2, bd2, bo2 = run(cost=10 / 1e4)
    summ(sw2, "sweep bn", 0); summ(bd2, "breakdown", 0); summ(bo2, "breakout", 0)
    print("\n=== PLACEBO (random up-pop, same short mechanic) ===")
    swp, _, _ = run(placebo=True)
    summ(swp, "sweep-placebo bn", 0)
    print(f"  -> real sweep bn {np.nanmean([x[0] for x in sw])*100:+.3f}% vs placebo {np.nanmean([x[0] for x in swp])*100:+.3f}%")


if __name__ == "__main__":
    main()
