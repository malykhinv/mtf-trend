"""SWEEP-FADE (закол): fade the FAILED breakout / stop-hunt at the 20d level.

Market-logic: price spikes THROUGH the 20d high (runs stops/liquidations) then rejects back
under the level -- a liquidity sweep. Most breakouts fail (continuation win-rate only 43%), so
FADE the sweep: go short on the reclaim-back-under, invalidate tight above the sweep wick's high
(if price re-takes the sweep extreme it was a real breakout -> we're wrong -> out), target the
reversal. The danger is the 'flyer': a real breakout that only paused -> fading it shorts the
+122% rocket; the tight invalidation must make the frequent small fades beat the rare flyer.

Detect sweep (poke above level, close back under within K h), enter short at the reclaim (also a
limit-retest variant), stop = sweep-high + buffer (sweep buffers & wider N-ATR stops tested),
targets = N x 1d-ATR down / hold-to-time. Market-relative short PnL, win/expectancy/%stop/%tp,
per-year (crude fade flipped by year -- is this stable?), worst-loss (flyer). Mirror downside
sweep -> fade long. IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.sweep_fade --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, KFAIL, MAXH = 20, 4, 48


def summarize(res, label):
    if not res:
        print(f"  {label:30s} (no trades)"); return
    r = np.array([x[0] for x in res]); dts = pd.to_datetime([x[3] for x in res])
    stop = np.mean([x[1] == "stop" for x in res]); tp = np.mean([x[1] == "tp" for x in res])
    w = r[r > 0]; l = r[r <= 0]
    yr = pd.Series(r, index=dts.year).groupby(level=0).mean() * 100
    ys = " ".join(f"{y}:{v:+.2f}" for y, v in yr.items())
    print(f"  {label:30s} n={len(r):5d} exp={r.mean()*100:+.2f}% win={np.mean(r>0):.1%} "
          f"avgW={w.mean()*100 if len(w) else 0:+.2f}% avgL={l.mean()*100 if len(l) else 0:+.2f}% "
          f"%stop={stop:.0%} %tp={tp:.0%} worst={r.min()*100:+.0f}%  [{ys}]")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv = (px(c) for c in ("close", "high", "low", "quote_volume"))
    idx, cols = close.index, close.columns
    btc = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)).to_numpy()
    hi_lvl = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    lo_lvl = low.resample("1D").min().rolling(LVL, min_periods=LVL // 2).min().shift(1).reindex(idx, method="ffill")
    above = close >= hi_lvl
    poke_up = (high >= hi_lvl) & (~above.shift(1).fillna(False)) & hi_lvl.notna()   # first bar tagging the level
    below = close <= lo_lvl
    poke_dn = (low <= lo_lvl) & (~below.shift(1).fillna(False)) & lo_lvl.notna()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    vs = (qv / qv.rolling(168, min_periods=48).mean())
    C, Hh, L, A, HL, LL, V = (x.to_numpy() for x in (close, high, low, atr, hi_lvl, lo_lvl, vs))

    def collect(poke, lvl_arr, side):
        """side=+1 upside sweep (fade short); side=-1 downside sweep (fade long)."""
        pk = poke.to_numpy(); out = []
        for si in range(len(cols)):
            for i in np.where(pk[:, si])[0]:
                if i + KFAIL + MAXH >= len(idx) or not (A[i, si] > 0):
                    continue
                lv = lvl_arr[i, si]
                # sweep = poked and reclaimed back within KFAIL bars
                jfail = None
                for t in range(i, i + KFAIL + 1):
                    if side > 0 and C[t, si] < lv:
                        jfail = t; break
                    if side < 0 and C[t, si] > lv:
                        jfail = t; break
                if jfail is None:
                    continue
                ext = np.nanmax(Hh[i:jfail + 1, si]) if side > 0 else np.nanmin(L[i:jfail + 1, si])
                out.append((si, i, jfail, C[jfail, si], ext, A[i, si], V[i, si], idx[jfail]))
        return out

    def sim(evs, side, stop_buf_atr, tp_atr):
        res = []
        for si, i, j, entry, ext, a, v, dt in evs:
            stop = ext + stop_buf_atr * a if side > 0 else ext - stop_buf_atr * a
            tp = entry - tp_atr * a if (side > 0 and tp_atr) else (entry + tp_atr * a if tp_atr else None)
            outcome, exitpx, exitbar = "time", C[j + MAXH, si], j + MAXH
            for t in range(j + 1, j + MAXH + 1):
                if side > 0:
                    if Hh[t, si] >= stop:
                        outcome, exitpx, exitbar = "stop", stop, t; break
                    if tp is not None and L[t, si] <= tp:
                        outcome, exitpx, exitbar = "tp", tp, t; break
                else:
                    if L[t, si] <= stop:
                        outcome, exitpx, exitbar = "stop", stop, t; break
                    if tp is not None and Hh[t, si] >= tp:
                        outcome, exitpx, exitbar = "tp", tp, t; break
            coin = exitpx / entry - 1.0; mkt = btc[exitbar] / btc[j] - 1.0
            pnl = (mkt - coin) if side > 0 else (coin - mkt)     # market-relative directional PnL
            res.append((pnl, outcome, si, dt))
        return res

    up = collect(poke_up, HL, +1); dn = collect(poke_dn, LL, -1)
    print(f"\nupside sweeps (fade SHORT): {len(up)}   downside sweeps (fade LONG): {len(dn)}\n")

    print("=== UPSIDE sweep -> fade SHORT (stop above sweep-high + buf, target N-ATR) ===")
    for sb in (0.0, 0.5, 1.0):
        for tp in (None, 1.0, 2.0, 3.0):
            summarize(sim(up, +1, sb, tp), f"stopbuf={sb}ATR tp={tp}")
        print()
    print("=== DOWNSIDE sweep -> fade LONG ===")
    for sb in (0.0, 0.5, 1.0):
        for tp in (None, 2.0):
            summarize(sim(dn, -1, sb, tp), f"stopbuf={sb}ATR tp={tp}")
        print()


if __name__ == "__main__":
    main()
