"""STOP-rule grid for the confirmed-hold breakout long: which stop keeps runners, cuts fails?

Prior finding (breakout_entryexit): hold-to-time beat every smart exit because tight trails/
stops cut the fat-tail runners. User asks to think through stop placements properly:
  PRICE stops (causal):  behind prior 1D-candle low (wide) | behind entry 1H-candle low (tight)
                         | behind the last 1H swing low (structural)
  TIMEOUT rules:         close-if-losing at timeout Tk (6/12/24h) -- cut losers, let winners run
                         move-to-BREAKEVEN at timeout Tk (6/12/24h) -- protect once matured
  BASELINE:              hold-to-time, no stop (T=48h)

Per confirmed-hold breakout we path-simulate each rule hour-by-hour to the realized exit and
score the MARKET-RELATIVE return (vs BTC over the actual holding window). Report per-trade mean
(the sleeve is mean-driven), median, win-rate, %stopped, runner tail (p90), mean-winner/loser,
and per-year mean -- to see whether a wide/structural stop cuts fails without killing runners.
IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.stop_grid --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, T, MAXH = 20, 4, 48        # 20d level, 4h confirm, 48h max hold


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    btc = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)).to_numpy()
    level = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = (above & (~above.shift(1).fillna(False)) & level.notna()).to_numpy()
    day_low = low.resample("1D").min().shift(1).reindex(idx, method="ffill").to_numpy()   # prior 1D candle low (causal)
    C, Hh, L, Ln = close.to_numpy(), high.to_numpy(), low.to_numpy(), level.to_numpy()

    # collect events with entry (dj), stop levels, forward path indices
    evs = []
    for si in range(len(cols)):
        for i in np.where(brk[:, si])[0]:
            dj = i + T
            if dj + MAXH >= len(idx) or not (C[dj, si] > 0) or not (C[dj, si] >= Ln[i, si]) or i < 24:
                continue
            swing = np.nanmin(L[dj - 12:dj + 1, si])          # last 1h swing low (12h)
            evs.append((si, dj, C[dj, si], day_low[dj, si], L[dj, si], swing, idx[dj]))
    print(f"confirmed-hold events: {len(evs)}\n")

    def sim(stop_frac_getter=None, be_to=None, closeloss_to=None):
        """Return array of market-relative realized returns under a stop rule."""
        out = []
        for si, dj, p0, s1d, s1h, sw, dt in evs:
            stop = stop_frac_getter(p0, s1d, s1h, sw) if stop_frac_getter else None
            exitbar = dj + MAXH; exitpx = C[dj + MAXH, si]
            for t in range(dj + 1, dj + MAXH + 1):
                k = t - dj
                cur_stop = stop
                if be_to is not None and k >= be_to:
                    cur_stop = p0 if cur_stop is None else max(cur_stop, p0)     # move to BE once matured
                if cur_stop is not None and L[t, si] <= cur_stop:
                    exitbar, exitpx = t, cur_stop; break
                if closeloss_to is not None and k == closeloss_to and C[t, si] < p0:
                    exitbar, exitpx = t, C[t, si]; break
            rel = (exitpx / p0 - 1.0) - (btc[exitbar] / btc[dj] - 1.0)
            out.append((rel, exitbar - dj, dt))
        return out

    rules = {
        "hold-to-time (base)": dict(),
        "stop 1D-low (wide)": dict(stop_frac_getter=lambda p, d, h, s: d),
        "stop 1H-low (tight)": dict(stop_frac_getter=lambda p, d, h, s: h),
        "stop 1H-swing12": dict(stop_frac_getter=lambda p, d, h, s: s),
        "closeloss@6h": dict(closeloss_to=6),
        "closeloss@12h": dict(closeloss_to=12),
        "closeloss@24h": dict(closeloss_to=24),
        "BE@6h": dict(be_to=6),
        "BE@12h": dict(be_to=12),
        "BE@24h": dict(be_to=24),
        "stop 1D-low + BE@24h": dict(stop_frac_getter=lambda p, d, h, s: d, be_to=24),
        "stop 1H-swing + BE@12h": dict(stop_frac_getter=lambda p, d, h, s: s, be_to=12),
        "stop 1H-swing + closeloss@24h": dict(stop_frac_getter=lambda p, d, h, s: s, closeloss_to=24),
    }
    print(f"  {'rule':30s} {'mean%':>7} {'med%':>7} {'win':>5} {'%stop':>6} {'p90%':>7} {'mWin%':>7} {'mLos%':>7}  by-year mean")
    for nm, kw in rules.items():
        res = sim(**kw)
        r = np.array([x[0] for x in res]); dts = pd.to_datetime([x[2] for x in res])
        stopped = np.mean([x[1] < MAXH for x in res]) * 100
        win = np.mean(r > 0); mwin = r[r > 0].mean() if (r > 0).any() else 0; mlos = r[r <= 0].mean() if (r <= 0).any() else 0
        yr = pd.Series(r, index=dts.year).groupby(level=0).mean() * 100
        ys = " ".join(f"{y}:{v:+.2f}" for y, v in yr.items())
        print(f"  {nm:30s} {r.mean()*100:+7.2f} {np.median(r)*100:+7.2f} {win:5.2f} {stopped:6.1f} "
              f"{np.percentile(r,90)*100:+7.2f} {mwin*100:+7.2f} {mlos*100:+7.2f}  {ys}")


if __name__ == "__main__":
    main()
