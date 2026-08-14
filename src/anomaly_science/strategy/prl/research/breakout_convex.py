"""Breakout convex execution: does the predictable RUNNER-size convert to R?

Enter long on a 20d-high breakout, small initial ATR stop + chandelier trailing stop,
long max-hold so the rare runners run. Report per-trade R expectancy, win, per-year,
for ALL breakouts vs RUNNER-SELECTED (high breakout-bar volume) vs a BLIND random-entry
control (same convex execution). Net of costs. IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.breakout_convex --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL = 20
COST = (4 + 2 + 1) / 1e4  # entry+exit one-side bps sum -> applied x2 in R terms below


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def convex_trade(c, h, l, entry_i, atr_i, stop_atr, trail_atr, W):
    """Return (ret, R) for a long convex trade entered at close[entry_i]."""
    entry = c[entry_i]
    if not (entry > 0) or not (atr_i > 0):
        return None
    risk = stop_atr * atr_i
    stop = entry - risk
    runmax = h[entry_i]
    end = min(len(c), entry_i + 1 + W)
    exit_px = c[end - 1] if end - 1 > entry_i else entry
    for j in range(entry_i + 1, end):
        runmax = max(runmax, h[j])
        stop = max(stop, runmax - trail_atr * atr_i)
        if l[j] <= stop:
            exit_px = stop
            break
    ret = exit_px / entry - 1.0 - 2 * COST
    return ret, ret / (risk / entry)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    close = panel.pivot(index="date", columns="symbol", values="close")
    high = panel.pivot(index="date", columns="symbol", values="high")
    low = panel.pivot(index="date", columns="symbol", values="low")
    qv = panel.pivot(index="date", columns="symbol", values="quote_volume")
    idx, cols = close.index, close.columns
    dhigh = high.resample("1D").max()
    level = dhigh.rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = above & (~above.shift(1).fillna(False)) & level.notna()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    vsurge = (qv / (_roll(qv, 168, "mean") + 1e-9)).to_numpy()

    C, Hh, Ll, A = (x.to_numpy() for x in (close, high, low, atr))
    bmask = brk.to_numpy()
    rng = np.random.default_rng(0)

    def collect(stop_atr, trail_atr, W, blind=False):
        rows = []
        for si in range(len(cols)):
            ev = np.where(bmask[:, si])[0]
            if blind:  # same count of random entries per symbol (non-event baseline)
                valid = np.where(np.isfinite(A[:, si]) & (A[:, si] > 0))[0]
                valid = valid[(valid > 200) & (valid < len(idx) - W - 2)]
                ev = rng.choice(valid, size=min(len(ev), len(valid)), replace=False) if len(valid) else []
            for i in ev:
                r = convex_trade(C[:, si], Hh[:, si], Ll[:, si], i, A[i, si], stop_atr, trail_atr, W)
                if r is None:
                    continue
                rows.append({"date": idx[i], "ret": r[0], "R": r[1], "vsurge": float(vsurge[i, si])})
        return pd.DataFrame(rows)

    print(f"\n=== convex execution grid (long breakout; stop/trail in ATR, W hours) ===")
    print(f"  {'cfg':>16} {'n':>6} {'winR':>5} {'meanR':>6} {'expR':>6} {'medRet':>7} | by-year meanR")
    for stop_atr, trail_atr, W in ((1.5, 3, 72), (1.5, 3, 168), (2, 4, 168), (1, 2, 72)):
        tr = collect(stop_atr, trail_atr, W)
        tr["year"] = pd.to_datetime(tr["date"]).dt.year
        yr = {y: g.R.mean() for y, g in tr.groupby("year")}
        ys = " ".join(f"{y}:{v:+.2f}" for y, v in yr.items())
        print(f"  s{stop_atr}/t{trail_atr}/W{W:<3} {len(tr):>6} {(tr.R>0).mean():>5.2f} "
              f"{tr.R.mean():>+6.2f} {tr.R.mean():>+6.2f} {tr.ret.median()*100:>+6.2f}% | ALL [{ys}]")
        # runner-selected (top-third vsurge) and blind, same config
        hi = tr[tr.vsurge >= tr.vsurge.quantile(2 / 3)]
        yb = {y: g.R.mean() for y, g in hi.assign(year=pd.to_datetime(hi.date).dt.year).groupby("year")}
        ys2 = " ".join(f"{y}:{v:+.2f}" for y, v in yb.items())
        print(f"  {'  hi-vsurge':>16} {len(hi):>6} {(hi.R>0).mean():>5.2f} {hi.R.mean():>+6.2f} "
              f"{hi.R.mean():>+6.2f} {hi.ret.median()*100:>+6.2f}% | [{ys2}]")
        bl = collect(stop_atr, trail_atr, W, blind=True)
        print(f"  {'  BLIND':>16} {len(bl):>6} {(bl.R>0).mean():>5.2f} {bl.R.mean():>+6.2f} "
              f"{bl.R.mean():>+6.2f} {bl.ret.median()*100:>+6.2f}%")
        print()


if __name__ == "__main__":
    main()
