"""Sweep-fade: how BIG does the reversal go, and can we target 2-5% off the TRUE 1d-ATR?

User wants a bigger take than the ~0.16% expectancy -- aim for 2-3-4-5%, scaled to the DAILY
ATR (not the hourly ATR used before). First we MEASURE the reversal: for each upside sweep
short, the max favorable down-move (MFE) within 48h, in % and in 1d-ATR units -> this bounds
what is targetable. Then a target ladder (k x 1d-ATR and fixed 2/3/4/5%) with the stop above
the sweep-high: hit-rate, avg winning take, expectancy (raw short AND market-relative), %stop,
by-year. Shows the real trade-off: bigger target = fewer hits, is it worth it? IS only.
Run: python -m anomaly_science.strategy.prl.research.sweep_target_atr --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, KFAIL, MAXH = 20, 4, 48


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
    # TRUE daily ATR(14) -> % of price, mapped causally to the hourly grid
    dh, dl, dc = high.resample("1D").max(), low.resample("1D").min(), close.resample("1D").last()
    dpc = dc.shift(1)
    dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
    datr = dtr.rolling(14, min_periods=7).mean().shift(1)
    datr_pct = (datr / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
    C, Hh, L, HL = (x.to_numpy() for x in (close, high, low, hi_lvl))
    pk = poke.to_numpy()

    evs = []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + KFAIL + MAXH >= len(idx) or not (datr_pct[i, si] > 0):
                continue
            lv = HL[i, si]; j = None
            for t in range(i, i + KFAIL + 1):
                if C[t, si] < lv:
                    j = t; break
            if j is None:
                continue
            ext = np.nanmax(Hh[i:j + 1, si])
            evs.append((si, j, C[j, si], ext, datr_pct[i, si], idx[j]))
    print(f"\nupside sweeps: {len(evs)}\n")

    # 1) measure the reversal MFE (favorable down-move) distribution
    revs, atrs, rev_in_atr = [], [], []
    for si, j, entry, ext, datrp, dt in evs:
        mlow = np.nanmin(L[j + 1:j + MAXH + 1, si])
        rev = (entry - mlow) / entry
        revs.append(rev); atrs.append(datrp); rev_in_atr.append(rev / datrp)
    revs = np.array(revs); atrs = np.array(atrs); rev_in_atr = np.array(rev_in_atr)
    print("=== how big is the reversal (max down-move within 48h after the sweep) ===")
    print(f"  1d-ATR of these coins: median {np.median(atrs)*100:.1f}%  (p25 {np.percentile(atrs,25)*100:.1f} / p75 {np.percentile(atrs,75)*100:.1f})")
    print(f"  reversal size %:   median {np.median(revs)*100:.1f}%  p75 {np.percentile(revs,75)*100:.1f}%  p90 {np.percentile(revs,90)*100:.1f}%")
    print(f"  reversal in 1dATR: median {np.median(rev_in_atr):.2f}x  p75 {np.percentile(rev_in_atr,75):.2f}x  p90 {np.percentile(rev_in_atr,90):.2f}x")
    for thr in (0.02, 0.03, 0.04, 0.05):
        print(f"    reach {thr*100:.0f}%+ reversal: {np.mean(revs >= thr):.0%} of sweeps")

    # 2) target ladder: exit at target (down) or stop (sweep-high) or 48h timeout
    def run(target_getter, label):
        raw, rel, hit, stp = [], [], [], []
        for si, j, entry, ext, datrp, dt in evs:
            tp = entry * (1 - target_getter(datrp)); stop = ext
            out, exitpx, xb = "time", C[j + MAXH, si], j + MAXH
            for t in range(j + 1, j + MAXH + 1):
                if Hh[t, si] >= stop:
                    out, exitpx, xb = "stop", stop, t; break
                if L[t, si] <= tp:
                    out, exitpx, xb = "tp", tp, t; break
            rshort = (entry - exitpx) / entry
            mrel = rshort + (btc[xb] / btc[j] - 1.0)          # market-relative short (add back mkt)
            raw.append(rshort); rel.append(mrel); hit.append(out == "tp"); stp.append(out == "stop")
        raw = np.array(raw); rel = np.array(rel)
        yr = pd.Series(rel, index=pd.to_datetime([e[5] for e in evs]).year).groupby(level=0).mean() * 100
        ys = " ".join(f"{y}:{v:+.2f}" for y, v in yr.items())
        w = rel[rel > 0]
        print(f"  {label:16s} rawExp={raw.mean()*100:+.3f}% relExp={rel.mean()*100:+.3f}% "
              f"%tp={np.mean(hit):.0%} %stop={np.mean(stp):.0%} avgWin={w.mean()*100 if len(w) else 0:+.2f}%  [{ys}]")

    print("\n=== target ladder off the 1d-ATR (stop = sweep-high) ===")
    for k in (0.5, 1.0, 1.5, 2.0):
        run(lambda d, k=k: k * d, f"{k}x 1dATR")
    print("  -- fixed % targets --")
    for pctt in (0.02, 0.03, 0.04, 0.05):
        run(lambda d, p=pctt: p, f"fixed {pctt*100:.0f}%")
    run(lambda d: 999.0, "hold-to-time")


if __name__ == "__main__":
    main()
