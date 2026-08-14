"""Breakdown-SHORT continuation with a STRUCTURAL stop placed AT ENTRY + zigzag-stretch sweep.

User: a short can't blow up -190% if a stop is placed at entry -- and it can be, structurally
(nearest swing high). But 'structural' is stretchy: a tight zigzag sits on noise (frequent stop-
outs), a wide one drags the stop far (big risk/trade). So place the initial stop at the nearest
ATR-zigzag swing high, TRAIL it down to new lower swing highs, and SWEEP the zigzag stretch
N in {1,2,3,4,5}. Report in R-multiples (comparable across N): expectancy, win, %stopped, avg
risk%, worst (should be ~-1R now, not -190%), per-year. Beta-neutral short P&L. On 2023-25 the
up-drift is a headwind; retest on 2022 bear. IS only. Run:
python -m anomaly_science.strategy.prl.research.breakdown_short_v2 --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

MAXH = 48


def zigzag(H, L, atr, thr):
    n = len(H); sh = np.full(n, np.nan); sl = np.full(n, np.nan)
    direction = 1; ext_i, ext_p = 0, H[0]
    for i in range(1, n):
        if direction == 1:
            if H[i] > ext_p:
                ext_i, ext_p = i, H[i]
            elif atr[ext_i] > 0 and (ext_p - L[i]) >= thr * atr[ext_i]:
                sh[i] = ext_p; direction = -1; ext_i, ext_p = i, L[i]
        else:
            if L[i] < ext_p:
                ext_i, ext_p = i, L[i]
            elif atr[ext_i] > 0 and (H[i] - ext_p) >= thr * atr[ext_i]:
                sl[i] = ext_p; direction = 1; ext_i, ext_p = i, H[i]
    return pd.Series(sh).ffill().to_numpy(), pd.Series(sl).ffill().to_numpy()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None); btcs = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1))
    btc_ret = btcs.pct_change(fill_method=None)
    beta = (ret.rolling(168, min_periods=48).cov(btc_ret).div(btc_ret.rolling(168, min_periods=48).var(), axis=0)).shift(1).clip(0, 3).fillna(1.0).to_numpy()
    btc = btcs.to_numpy()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    C, Hh, L, A = (x.to_numpy() for x in (close, high, low, atr))

    print("\n=== breakdown-short + structural stop-at-entry + trail, by zigzag stretch N ===")
    print(f"  {'N(stretch)':>10} {'n':>6} {'exp%':>7} {'expR':>6} {'win':>5} {'%stop':>6} {'avgRisk%':>9} {'worstR':>7}   by-year(R)")
    for thr in (1, 2, 3, 4, 5):
        rows = []
        for si in range(len(cols)):
            SH, SL = zigzag(Hh[:, si], L[:, si], A[:, si], thr)
            belowSL = C[:, si] < SL
            fresh = belowSL & ~np.concatenate([[False], belowSL[:-1]])   # first bar below recent swing low
            for e in np.where(fresh)[0]:
                if e + MAXH >= len(idx) or not (C[e, si] > 0) or not np.isfinite(SH[e]) or not (A[e, si] > 0):
                    continue
                entry = C[e, si]; init_stop = SH[e]
                if init_stop <= entry:
                    continue
                risk = (init_stop - entry) / entry
                if risk <= 0 or risk > 1.0:                              # skip absurd (>100%) stops
                    continue
                active = init_stop; xb, expx = e + MAXH, C[e + MAXH, si]
                for t in range(e + 1, e + MAXH + 1):
                    if np.isfinite(SH[t]):
                        active = min(active, SH[t])
                    if C[t, si] > active:
                        xb, expx = t, C[t, si]; break
                coin = expx / entry - 1.0
                bn = beta[e, si] * (btc[xb] / btc[e] - 1.0) - coin - 6 / 1e4
                rows.append((bn, bn / risk, risk, idx[e], C[xb, si] > active if xb < e + MAXH else False))
        rows = [r for r in rows if np.isfinite(r[0]) and np.isfinite(r[1])]
        if not rows:
            print(f"  {thr:>10} (none)"); continue
        bn = np.array([r[0] for r in rows]); R = np.array([r[1] for r in rows]); rk = np.array([r[2] for r in rows])
        stopped = np.mean([r[4] for r in rows]); dts = pd.to_datetime([r[3] for r in rows])
        ys = " ".join(f"{y}:{g.mean():+.2f}" for y, g in pd.Series(R, index=dts.year).groupby(level=0))
        print(f"  {thr:>10} {len(bn):>6} {bn.mean()*100:+7.3f} {R.mean():+6.2f} {(bn>0).mean():5.0%} {stopped:6.0%} "
              f"{rk.mean()*100:9.1f} {np.percentile(R,1):+7.1f}   [{ys}]")


if __name__ == "__main__":
    main()
