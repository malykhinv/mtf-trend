"""Breakout-LONG continuation with a STRUCTURAL stop-at-entry + trail + zigzag-stretch sweep.

Mirror of breakdown_short_v2 for the long side: prior long-breakout work found hold-to-time best
and stops/trails hurt -- but those were TIGHT ATR stops. Does a WIDE STRUCTURAL swing-low trail
(loose enough not to clip the fat-tail runner, but capping losers) help, at the right stretch?
Enter long on a break above the ATR-zigzag swing high, place the initial stop at the nearest swing
LOW, trail it UP to new higher swing lows, sweep stretch N in {1..5}. Report raw-long AND beta-
neutral expectancy, win, %stopped, avg risk, worstR, per-year, plus a HOLD-TO-TIME reference row.
IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.breakout_long_structural --n 150
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

    print("\n=== breakout-LONG + structural stop-at-entry + trail, by zigzag stretch N ===")
    print(f"  {'N':>4} {'exit':>10} {'n':>6} {'raw%':>7} {'bn%':>7} {'expR':>6} {'win':>5} {'%stop':>6} {'risk%':>6} {'worstR':>7}   by-year(bn%)")
    for thr in (1, 2, 3, 4, 5):
        trail_rows, hold_rows = [], []
        for si in range(len(cols)):
            SH, SL = zigzag(Hh[:, si], L[:, si], A[:, si], thr)
            aboveSH = C[:, si] > SH
            fresh = aboveSH & ~np.concatenate([[False], aboveSH[:-1]])   # fresh break above recent swing high
            for e in np.where(fresh)[0]:
                if e + MAXH >= len(idx) or not (C[e, si] > 0) or not np.isfinite(SL[e]) or not (A[e, si] > 0):
                    continue
                entry = C[e, si]; init_stop = SL[e]
                if init_stop >= entry:
                    continue
                risk = (entry - init_stop) / entry
                if risk <= 0 or risk > 1.0:
                    continue
                # structural trailing long: stop ratchets UP to higher swing lows; exit when close < stop
                active = init_stop; xb, expx = e + MAXH, C[e + MAXH, si]
                for t in range(e + 1, e + MAXH + 1):
                    if np.isfinite(SL[t]):
                        active = max(active, SL[t])
                    if C[t, si] < active:
                        xb, expx = t, C[t, si]; break
                coin = expx / entry - 1.0
                bn = coin - beta[e, si] * (btc[xb] / btc[e] - 1.0) - 6 / 1e4
                stopped = xb < e + MAXH
                trail_rows.append((coin - 6 / 1e4, bn, bn / risk, risk, idx[e], stopped))
                # hold-to-time reference (same entry, no stop)
                xh = e + MAXH; coinh = C[xh, si] / entry - 1.0
                bnh = coinh - beta[e, si] * (btc[xh] / btc[e] - 1.0) - 6 / 1e4
                hold_rows.append((coinh - 6 / 1e4, bnh, idx[e]))
        def fin(rows, idxs):
            return [r for r in rows if all(np.isfinite(r[k]) for k in idxs)]
        trail_rows = fin(trail_rows, [0, 1, 2, 3]); hold_rows = fin(hold_rows, [0, 1])
        if not trail_rows:
            print(f"  {thr:>4} (none)"); continue
        raw = np.array([r[0] for r in trail_rows]); bn = np.array([r[1] for r in trail_rows])
        R = np.array([r[2] for r in trail_rows]); rk = np.array([r[3] for r in trail_rows])
        stp = np.mean([r[5] for r in trail_rows]); dts = pd.to_datetime([r[4] for r in trail_rows])
        ys = " ".join(f"{y}:{g.mean()*100:+.2f}" for y, g in pd.Series(bn, index=dts.year).groupby(level=0))
        print(f"  {thr:>4} {'TRAIL':>10} {len(bn):>6} {raw.mean()*100:+7.3f} {bn.mean()*100:+7.3f} {R.mean():+6.2f} "
              f"{(bn>0).mean():5.0%} {stp:6.0%} {rk.mean()*100:6.1f} {np.percentile(R,1):+7.1f}   [{ys}]")
        hraw = np.array([r[0] for r in hold_rows]); hbn = np.array([r[1] for r in hold_rows])
        hdts = pd.to_datetime([r[2] for r in hold_rows])
        hys = " ".join(f"{y}:{g.mean()*100:+.2f}" for y, g in pd.Series(hbn, index=hdts.year).groupby(level=0))
        print(f"  {thr:>4} {'hold-time':>10} {len(hbn):>6} {hraw.mean()*100:+7.3f} {hbn.mean()*100:+7.3f} {'':>6} "
              f"{(hbn>0).mean():5.0%} {'':>6} {'':>6} {'':>7}   [{hys}]")


if __name__ == "__main__":
    main()
