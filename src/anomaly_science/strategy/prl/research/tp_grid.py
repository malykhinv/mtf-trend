"""TAKE-PROFIT grid for the confirmed-hold breakout long (user-proposed TP placements).

Rules tested (exit at the TP if price trades up to it during the 48h hold; else exit at market
at 48h):
  ROUND NUMBERS:  nearest round level above entry, and the 2nd round level (decade grid, e.g.
                  0.073 -> 0.08 / 0.09; 67,340 -> 70k / 80k) -- do prices stall at round numbers?
  ATR GRID:       TP at entry + N x (1-day ATR), N = 1,2,3,4 -- a volatility-scaled target ladder.
Baseline = hold-to-time (no TP). Market-relative returns. Report expectancy, win-rate, avg take,
% of trades that hit the TP, and how much of the fat runner tail (p90/p99) the TP gives up. Also
the DISTANCE the round/ATR TP sits from entry (what the TP depends on). IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.tp_grid --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, T, HOLD = 20, 4, 48


def next_round(p, mult):
    """mult=1 -> next decade round (0.073->0.08), mult=2 -> the one after (0.09)."""
    step = 10.0 ** np.floor(np.log10(p))
    return (np.floor(p / step) + mult) * step


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
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    C, Hh, L, A, Ln = close.to_numpy(), high.to_numpy(), low.to_numpy(), atr.to_numpy(), level.to_numpy()

    ev = []
    for si in range(len(cols)):
        for i in np.where(brk[:, si])[0]:
            dj = i + T
            if dj + HOLD >= len(idx) or not (C[dj, si] > 0) or not (C[dj, si] >= Ln[i, si]) or not (A[i, si] > 0):
                continue
            ev.append((si, dj, C[dj, si], A[i, si], idx[dj]))
    print(f"confirmed-hold trades: {len(ev)}\n")

    def run(tp_getter):
        rets, hits, dists = [], [], []
        for si, dj, p0, a, dt in ev:
            tp = tp_getter(p0, a)
            exitbar, exitpx, hit = dj + HOLD, C[dj + HOLD, si], 0
            if tp is not None and tp > p0:
                for t in range(dj + 1, dj + HOLD + 1):
                    if Hh[t, si] >= tp:
                        exitbar, exitpx, hit = t, tp, 1; break
            rel = (exitpx / p0 - 1.0) - (btc[exitbar] / btc[dj] - 1.0)
            rets.append(rel); hits.append(hit)
            if tp is not None:
                dists.append(tp / p0 - 1.0)
        return np.array(rets), np.mean(hits), (np.array(dists) if dists else None)

    def show(nm, rets, hitrate, dists):
        w = rets[rets > 0]; l = rets[rets <= 0]
        aw = w.mean() if len(w) else 0; al = l.mean() if len(l) else 0
        dtxt = ""
        if dists is not None:
            dtxt = f" | TPdist med {np.median(dists)*100:+.1f}% (p25 {np.percentile(dists,25)*100:+.0f}/p75 {np.percentile(dists,75)*100:+.0f})"
        print(f"  {nm:20s} exp={rets.mean()*100:+.2f}% win={np.mean(rets>0):.1%} avgTake={aw*100:+.2f}% "
              f"avgLoss={al*100:+.2f}% TPhit={hitrate:.0%} p90={np.percentile(rets,90)*100:+.1f}% p99={np.percentile(rets,99)*100:+.1f}%{dtxt}")

    print("=== take-profit rules (market-relative; baseline = no TP, hold 48h) ===")
    r, h, d = run(lambda p, a: None); show("hold-to-time (base)", r, 0.0, None)
    r, h, d = run(lambda p, a: next_round(p, 1)); show("TP nearest round", r, h, d)
    r, h, d = run(lambda p, a: next_round(p, 2)); show("TP 2nd round", r, h, d)
    for N in (1, 2, 3, 4):
        r, h, d = run(lambda p, a, N=N: p + N * a); show(f"TP {N}x 1d-ATR", r, h, d)
    # round-number TP but let the rest run if TP is far (only take if within reach)
    r, h, d = run(lambda p, a: min(next_round(p, 1), p + 3 * a)); show("TP min(round, 3ATR)", r, h, d)


if __name__ == "__main__":
    main()
