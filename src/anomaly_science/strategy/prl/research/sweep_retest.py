"""Sweep-fade with a LIMIT-RETEST entry (user's idea: short the bounce back into the level).

Diagnosis from sweep_target_atr: the reversal is big (median 5.5%, 81% reach 2%) but a stop at
the sweep-high is run FIRST in 56-81% of cases -- price double-taps its own high before reversing.
Fix (user): don't short the reclaim at a low price next to the stop; place a SHORT LIMIT on the
bounce back up toward the broken level / sweep-high (a retest). A fill gives a higher entry (more
reversal to capture) and a tighter stop just above the sweep-high -> better R:R, and it sidesteps
the immediate double-tap stop.

After the sweep (reclaim under level at bar j) we post a short limit at price = level + f*(sweep_high
- level), f in {0 (at level), 0.5, 1.0 (at sweep-high)}; if price trades up to it within RETEST_WIN
hours we are filled short; else the trade is skipped (unfilled). Stop = sweep_high + buf*1dATR;
target = k*1d-ATR down. Report fill-rate, expectancy (raw short & market-relative), avg take, %stop,
by-year, vs the immediate entry. IS only. Run: python -m anomaly_science.strategy.prl.research.sweep_retest --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, KFAIL, RETEST_WIN, MAXH = 20, 4, 12, 48


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    btc = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)).to_numpy()
    hi_lvl = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= hi_lvl
    poke = (high >= hi_lvl) & (~above.shift(1).fillna(False)) & hi_lvl.notna()
    dh, dl, dc = high.resample("1D").max(), low.resample("1D").min(), close.resample("1D").last()
    dpc = dc.shift(1)
    dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
    datr_pct = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
    C, Hh, L, HL = (x.to_numpy() for x in (close, high, low, hi_lvl))
    pk = poke.to_numpy()

    evs = []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + KFAIL + RETEST_WIN + MAXH >= len(idx) or not (datr_pct[i, si] > 0):
                continue
            lv = HL[i, si]; j = None
            for t in range(i, i + KFAIL + 1):
                if C[t, si] < lv:
                    j = t; break
            if j is None:
                continue
            ext = np.nanmax(Hh[i:j + 1, si])
            evs.append((si, j, C[j, si], lv, ext, datr_pct[i, si], idx[j]))
    print(f"\nupside sweeps: {len(evs)}\n")

    def run(entry_mode, f, k_tp, buf, label):
        raw, rel, filled, hit, stp = [], [], 0, [], []
        for si, j, reclaim, lv, ext, datrp, dt in evs:
            if entry_mode == "immediate":
                entry = reclaim; start = j + 1
            else:                                        # limit-retest: short limit at lv+f*(ext-lv)
                limitp = lv + f * (ext - lv); entry = None
                for t in range(j + 1, j + 1 + RETEST_WIN):
                    if Hh[t, si] >= limitp:
                        entry = limitp; start = t + 1; break
                if entry is None:
                    continue
            filled += 1
            stop = ext + buf * datrp * entry; tp = entry * (1 - k_tp * datrp)
            out, exitpx, xb = "time", C[min(start + MAXH, len(idx) - 1), si], min(start + MAXH, len(idx) - 1)
            for t in range(start, min(start + MAXH, len(idx))):
                if Hh[t, si] >= stop:
                    out, exitpx, xb = "stop", stop, t; break
                if L[t, si] <= tp:
                    out, exitpx, xb = "tp", tp, t; break
            rshort = (entry - exitpx) / entry
            mrel = rshort + (btc[xb] / btc[j] - 1.0)
            raw.append(rshort); rel.append(mrel); hit.append(out == "tp"); stp.append(out == "stop")
        raw = np.array(raw); rel = np.array(rel)
        if len(raw) == 0:
            print(f"  {label:34s} (no fills)"); return
        yr = pd.Series(rel, index=pd.to_datetime([e[6] for e in evs[:0]] or [pd.Timestamp('2024')] * 0)).groupby(level=0).mean() if False else None
        dts = pd.to_datetime([dt for (si, j, r, lv, ext, d, dt) in evs])  # not aligned to fills; recompute below
        w = rel[rel > 0]
        print(f"  {label:34s} fill={filled/len(evs):.0%} rawExp={raw.mean()*100:+.3f}% relExp={rel.mean()*100:+.3f}% "
              f"%tp={np.mean(hit):.0%} %stop={np.mean(stp):.0%} avgWin={w.mean()*100 if len(w) else 0:+.2f}%")

    def run_yr(entry_mode, f, k_tp, buf, label):
        rows = []
        for si, j, reclaim, lv, ext, datrp, dt in evs:
            if entry_mode == "immediate":
                entry = reclaim; start = j + 1
            else:
                limitp = lv + f * (ext - lv); entry = None
                for t in range(j + 1, j + 1 + RETEST_WIN):
                    if Hh[t, si] >= limitp:
                        entry = limitp; start = t + 1; break
                if entry is None:
                    continue
            stop = ext + buf * datrp * entry; tp = entry * (1 - k_tp * datrp)
            out, exitpx, xb = "time", C[min(start + MAXH, len(idx) - 1), si], min(start + MAXH, len(idx) - 1)
            for t in range(start, min(start + MAXH, len(idx))):
                if Hh[t, si] >= stop:
                    out, exitpx, xb = "stop", stop, t; break
                if L[t, si] <= tp:
                    out, exitpx, xb = "tp", tp, t; break
            rshort = (entry - exitpx) / entry
            rows.append((rshort + (btc[xb] / btc[j] - 1.0), dt))
        if not rows:
            print(f"  {label:34s} (no fills)"); return
        rel = np.array([r[0] for r in rows]); yr = pd.Series(rel, index=pd.to_datetime([r[1] for r in rows]).year).groupby(level=0).mean() * 100
        ys = " ".join(f"{y}:{v:+.2f}" for y, v in yr.items())
        w = rel[rel > 0]
        print(f"  {label:34s} n={len(rel):5d} relExp={rel.mean()*100:+.3f}% avgWin={w.mean()*100 if len(w) else 0:+.2f}%  [{ys}]")

    print("=== immediate vs limit-retest entry (stop=sweep-high+0.25*1dATR, target 1x1dATR) ===")
    run_yr("immediate", 0, 1.0, 0.25, "immediate @reclaim")
    for f in (0.0, 0.5, 1.0):
        run_yr("retest", f, 1.0, 0.25, f"retest @ lvl+{f:.1f}*(high-lvl)")
    print("\n=== best retest (f=0.5) target ladder ===")
    for k in (0.5, 1.0, 1.5, 2.0):
        run_yr("retest", 0.5, k, 0.25, f"retest f=0.5 tp={k}xATR")


if __name__ == "__main__":
    main()
