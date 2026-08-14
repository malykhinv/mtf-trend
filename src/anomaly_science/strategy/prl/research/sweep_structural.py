"""Sweep-fade on STRUCTURAL swing levels (ATR-zigzag), not the arbitrary 20d-high.

User: the 20d-high is a number pulled from thin air applied to the whole market -- levels differ;
the ones worth fading are formed by BIG SWEEPING rises and falls. So detect levels structurally:
an ATR-zigzag with reversal threshold N x (1h-ATR), N a natural number (2/3/4/5). Each confirmed
swing HIGH is a level; its FORMATION impulse (up-leg size in ATR) tells sweeping vs shallow. Then
fade the sweep of that level with the proven mechanic (limit-retest short at the level, CLOSE-based
stop above the sweep-high, target 1x 1d-ATR). Compare: all swing levels vs big-impulse ("sweeping")
levels only; sweep the zigzag N. Does level QUALITY strengthen the fade? IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.sweep_structural --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

KFAIL, RETEST_WIN, MAXH, LIVE = 4, 12, 48, 240      # sweep-fail window, retest window, hold, level lifetime (h)


def zigzag_highs(H, L, atr, thr):
    """Causal ATR-zigzag -> list of swing highs (pivot_idx, price, prior_low, impulse_atr, confirm_idx)."""
    n = len(H); out = []
    s = 0
    while s < n and not (np.isfinite(H[s]) and np.isfinite(L[s])):   # skip leading NaN (late-launch coins)
        s += 1
    if s >= n - 1:
        return out
    direction = 1                       # +1 seeking high, -1 seeking low
    ext_i, ext_p = s, H[s]              # running extreme
    last_low_p = L[s]                   # most recent confirmed swing low price (for impulse)
    for i in range(s + 1, n):
        if not (np.isfinite(H[i]) and np.isfinite(L[i])):
            continue
        if direction == 1:
            if H[i] > ext_p:
                ext_i, ext_p = i, H[i]
            elif atr[ext_i] > 0 and (ext_p - L[i]) >= thr * atr[ext_i]:
                imp = (ext_p - last_low_p) / atr[ext_i] if atr[ext_i] > 0 else 0.0   # up-leg in ATR
                out.append((ext_i, ext_p, last_low_p, imp, i))                       # confirmed at bar i
                direction = -1; ext_i, ext_p = i, L[i]
        else:
            if L[i] < ext_p:
                ext_i, ext_p = i, L[i]
            elif atr[ext_i] > 0 and (H[i] - ext_p) >= thr * atr[ext_i]:
                last_low_p = ext_p
                direction = 1; ext_i, ext_p = i, H[i]
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    btc = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)).to_numpy()
    pc = close.shift(1)
    atr1h = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    dh, dl, dc = high.resample("1D").max(), low.resample("1D").min(), close.resample("1D").last()
    dpc = dc.shift(1)
    dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
    datr_pct = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
    C, Hh, L, AT = (x.to_numpy() for x in (close, high, low, atr1h))

    def collect(thr):
        rows = []
        for si in range(len(cols)):
            piv = zigzag_highs(Hh[:, si], L[:, si], AT[:, si], thr)
            for (pi, Hlvl, plow, imp, ci) in piv:
                if not (datr_pct[ci, si] > 0):
                    continue
                # find first sweep of the level after confirmation, within LIVE hours
                swept = None
                for t in range(ci, min(ci + LIVE, len(idx))):
                    if Hh[t, si] >= Hlvl:               # price pokes back above the swing high
                        # require reclaim under within KFAIL
                        jf = None
                        for u in range(t, min(t + KFAIL + 1, len(idx))):
                            if C[u, si] < Hlvl:
                                jf = u; break
                        if jf is not None:
                            ext = np.nanmax(Hh[t:jf + 1, si]); swept = (t, jf, ext)
                        break
                if swept is None:
                    continue
                t, jf, ext = swept
                if jf + RETEST_WIN + MAXH >= len(idx):
                    continue
                datrp = datr_pct[jf, si]
                # limit-retest short at the level (H), close-based stop above sweep-high, target 1d-ATR
                entry = None
                for u in range(jf + 1, jf + 1 + RETEST_WIN):
                    if Hh[u, si] >= Hlvl:
                        entry = Hlvl; start = u + 1; break
                if entry is None:
                    continue
                stop = ext + 0.25 * datrp * entry; tp = entry * (1 - 1.0 * datrp)
                out, exitpx, xb = "time", C[min(start + MAXH, len(idx) - 1), si], min(start + MAXH, len(idx) - 1)
                for u in range(start, min(start + MAXH, len(idx))):
                    if C[u, si] >= stop:
                        out, exitpx, xb = "stop", C[u, si], u; break
                    if L[u, si] <= tp:
                        out, exitpx, xb = "tp", tp, u; break
                mrel = (entry - exitpx) / entry + (btc[xb] / btc[jf] - 1.0) - 6 / 1e4
                rows.append(dict(mrel=mrel, imp=imp, out=out, date=idx[start - 1]))
        return pd.DataFrame(rows)

    from pandas import to_datetime
    print("\n=== sweep-fade on ATR-zigzag structural levels (close stop, 1dATR target, net 6bps) ===")
    print(f"  {'thr(N*1hATR)':>12} {'n':>6} {'exp':>8} {'win':>5} {'%stop':>6}   by-year")
    for thr in (2, 3, 4, 5):
        d = collect(thr)
        if len(d) == 0:
            print(f"  {thr:>12} (none)"); continue
        yr = pd.Series(d.mrel.values, index=to_datetime(d.date).dt.year).groupby(level=0).mean() * 100
        ys = " ".join(f"{y}:{v:+.2f}" for y, v in yr.items())
        print(f"  {thr:>12} {len(d):>6} {d.mrel.mean()*100:+8.3f}% {(d.mrel>0).mean():5.0%} "
              f"{(d.out=='stop').mean():6.0%}   [{ys}]")
        # split by impulse (sweeping = big up-leg) quartiles
        d = d.dropna(subset=["imp"])
        d["q"] = pd.qcut(d.imp, 4, labels=["shallow", "q2", "q3", "sweeping"], duplicates="drop")
        for q, gg in d.groupby("q", observed=True):
            yq = pd.Series(gg.mrel.values, index=to_datetime(gg.date).dt.year).groupby(level=0).mean() * 100
            print(f"        impulse {str(q):9s} n={len(gg):5d} exp={gg.mrel.mean()*100:+.3f}% win={(gg.mrel>0).mean():.0%}  "
                  + " ".join(f"{y}:{v:+.1f}" for y, v in yq.items()))
        print()


if __name__ == "__main__":
    main()
