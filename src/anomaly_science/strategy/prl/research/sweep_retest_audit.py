"""Look-ahead / realism AUDIT for the sweep-fade retest edge (gate: it looked too good).

The retest short showed +0.557%/trade and a sleeve Sharpe ~3. Before trusting it we stress every
optimistic assumption:
  base            : limit fills at the sweep-high, stop fills at the stop level, 6bps, no delay
  conservative-fill: enter at the TOUCH BAR'S CLOSE (not the exact limit) -- kills any "sold the
                     exact local high" optimism
  stop-slippage   : stop fills 0.25*1dATR WORSE (gap-through)
  entry+1bar      : enter one bar LATER than the retest touch (no same-bar fill)
  cost 10bps      : higher friction
  shuffle         : keep entries/exits but PERMUTE the per-trade PnL across trades -> the sleeve
                    Sharpe must collapse to ~0 (proves the book isn't a construction artifact)
Report per-trade market-relative expectancy, %tp/%stop, by-year, and (for shuffle) the sleeve
Sharpe. IS only. Run: python -m anomaly_science.strategy.prl.research.sweep_retest_audit --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, KFAIL, RETEST_WIN, MAXH = 20, 4, 12, 48
FBUF, KTP, F = 0.25, 1.0, 1.0


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

    def trades(fill_mode="limit", entry_delay=0, stop_slip=0.0, cost=6 / 1e4, stop_close=False):
        rows = []
        for si in range(len(cols)):
            for i in np.where(pk[:, si])[0]:
                if i + KFAIL + RETEST_WIN + MAXH + 2 >= len(idx) or not (datr_pct[i, si] > 0):
                    continue
                lv = HL[i, si]; j = None
                for t in range(i, i + KFAIL + 1):
                    if C[t, si] < lv:
                        j = t; break
                if j is None:
                    continue
                ext = np.nanmax(Hh[i:j + 1, si]); datrp = datr_pct[i, si]
                limitp = lv + F * (ext - lv); touch = None
                for t in range(j + 1, j + 1 + RETEST_WIN):
                    if Hh[t, si] >= limitp:
                        touch = t; break
                if touch is None:
                    continue
                st = touch + 1 + entry_delay
                if st >= len(idx):
                    continue
                entry = limitp if fill_mode == "limit" else C[touch, si]     # conservative: touch-bar close
                if entry <= 0:
                    continue
                stop = ext + FBUF * datrp * entry; tp = entry * (1 - KTP * datrp)
                out, exitpx, xb = "time", C[min(st + MAXH, len(idx) - 1), si], min(st + MAXH, len(idx) - 1)
                for t in range(st, min(st + MAXH, len(idx))):
                    if stop_close:                                   # exit at CLOSE if a bar closes above stop
                        if C[t, si] >= stop:
                            out, exitpx, xb = "stop", C[t, si], t; break
                    elif Hh[t, si] >= stop:                          # intrabar stop (slippage-exposed)
                        exitpx = stop * (1 + stop_slip * datrp); out, xb = "stop", t; break
                    if L[t, si] <= tp:
                        out, exitpx, xb = "tp", tp, t; break
                mrel = (entry - exitpx) / entry + (btc[xb] / btc[j] - 1.0) - cost
                rows.append((mrel, out, idx[st - 1], idx[xb]))
        return rows

    def report(rows, label):
        if not rows:
            print(f"  {label:22s} (none)"); return
        r = np.array([x[0] for x in rows])
        yr = pd.Series(r, index=pd.to_datetime([x[2] for x in rows]).year).groupby(level=0).mean() * 100
        ys = " ".join(f"{y}:{v:+.2f}" for y, v in yr.items())
        tp = np.mean([x[1] == "tp" for x in rows]); stp = np.mean([x[1] == "stop" for x in rows])
        print(f"  {label:22s} n={len(r):5d} exp={r.mean()*100:+.3f}% %tp={tp:.0%} %stop={stp:.0%}  [{ys}]")
        return rows

    print("\n=== realism / look-ahead audit (per-trade market-relative, net) ===")
    base = report(trades(), "base (limit,6bps)")
    report(trades(fill_mode="close"), "conservative-fill")
    report(trades(entry_delay=1), "entry +1 bar")
    report(trades(cost=10 / 1e4), "cost 10bps")
    print("  -- STOP-slippage sweep (fraction of 1d-ATR added to the stop fill; 76% of trades stop) --")
    for sl in (0.02, 0.05, 0.10, 0.20):
        report(trades(stop_slip=sl), f"stop-slip {sl:.2f}ATR")
    report(trades(fill_mode="close", stop_slip=0.05, entry_delay=1, cost=10 / 1e4), "realistic-ALL(.05slip)")
    print("  -- CLOSE-based stop (exit at bar close above stop; no intrabar slippage knife) --")
    report(trades(stop_close=True), "stop-on-close 6bps")
    report(trades(stop_close=True, fill_mode="close", entry_delay=1, cost=10 / 1e4), "stop-on-close ALL-cons")

    # shuffle control: permute per-trade PnL across trades -> sleeve Sharpe must collapse
    rows = base
    hpos = {t: k for k, t in enumerate(idx)}
    def sleeve_sharpe(rows, shuffle=False):
        r = np.array([x[0] for x in rows]).copy()
        if shuffle:
            np.random.default_rng(0).shuffle(r)
        pnl = np.zeros(len(idx))
        for (val, (_, out, et, xt)) in zip(r, rows):
            pnl[hpos[xt]] += val
        d = pd.Series(pnl, index=idx).resample("1D").sum()
        return d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    print("\n=== shuffle control (equal-weight sleeve) ===")
    print(f"  real sleeve Sharpe    {sleeve_sharpe(rows):+.2f}")
    print(f"  shuffled-PnL Sharpe   {sleeve_sharpe(rows, shuffle=True):+.2f}  (must be ~0)")


if __name__ == "__main__":
    main()
