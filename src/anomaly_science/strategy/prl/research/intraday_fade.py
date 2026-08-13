"""Event-driven intraday reversal fade + SL->breakeven trigger grid (user direction).

The daily signal inverts intraday (negative 5m/15m IC = short-term reversal). Here we
turn that into an EVENT-driven trade: when a name over-extends short-term relative to
the market (residual move z-score beyond a threshold), fade it (short a pump, long a
dump). Each trade has an initial ATR stop, a mean-reversion target, and a time stop.

We then test moving the stop to breakeven under different triggers (user's list):
ATR move in favor, break of the prior bar, two consecutive favorable bars, or none.
Breakeven raises win-rate but can cap winners -> we report net expectancy, not just win%.

Data: existing 1m resampled to `--tf`, IS only (2025-06..2026-01; reserved OOS untouched).
CAVEAT: ~7 months of a single (2025 bear) regime -> high overfitting risk; this is a
hypothesis probe, not a validated edge. Run:
  python -m anomaly_science.strategy.prl.research.intraday_fade --tf 15min --n 50
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research import fine_tier as ft

OUT = Path(".output/results/prl_coarse")


def _atr(high, low, close, win=14):
    pc = close.shift(1)
    tr = pd.concat([(high - low), (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(win, min_periods=win // 2).mean()


def _simulate_symbol(o, h, l, c, atr, sig, p):
    """Sequential per-symbol fade sim. Returns list of trade dicts."""
    n = len(c)
    trades = []
    i = p["warm"]
    while i < n - 1:
        z = sig[i]
        side = -1 if z >= p["thr"] else (1 if z <= -p["thr"] else 0)
        if side == 0 or not np.isfinite(atr[i]) or atr[i] <= 0:
            i += 1
            continue
        entry = o[i + 1]
        if not (entry > 0):
            i += 1
            continue
        a = atr[i]
        risk = p["sl"] * a
        sl = entry - side * risk               # against the fade
        tgt = entry + side * p["tgt"] * a       # reversion in favor
        be_done = False
        favor_bars = 0
        exit_px, exit_reason = None, None
        j = i + 1
        end = min(n, i + 1 + p["hold"])
        while j < end:
            hi, lo, cl = h[j], l[j], c[j]
            # breakeven trigger (before stop check so it can protect this bar)
            if not be_done and p["be"] != "none":
                trig = False
                if p["be"] == "atr":
                    trig = side * (cl - entry) >= p["be_atr"] * a
                elif p["be"] == "prev":
                    trig = (cl < l[j - 1]) if side < 0 else (cl > h[j - 1])
                elif p["be"] == "two":
                    fav = (cl < o[j]) if side < 0 else (cl > o[j])
                    favor_bars = favor_bars + 1 if fav else 0
                    trig = favor_bars >= 2
                if trig:
                    sl = entry
                    be_done = True
            # stop check (intrabar): short stops if high>=sl; long stops if low<=sl
            if (side < 0 and hi >= sl) or (side > 0 and lo <= sl):
                exit_px, exit_reason = sl, ("be" if be_done and sl == entry else "sl")
                break
            # target check
            if (side < 0 and lo <= tgt) or (side > 0 and hi >= tgt):
                exit_px, exit_reason = tgt, "tgt"
                break
            j += 1
        if exit_px is None:
            exit_px, exit_reason = c[min(j, n - 1)], "time"
        r = side * (exit_px / entry - 1.0)
        trades.append({"r_pct": r, "R": r / (risk / entry), "reason": exit_reason})
        i = j + p["cooldown"]           # no overlap + cooldown
    return trades


def run(panel, tf, be, thr=2.0, sl=1.0, tgt=1.5, hold=16, cooldown=2):
    close = ft.fx.pn.pivot(panel, "close")
    open_ = ft.fx.pn.pivot(panel, "open").reindex_like(close)
    high = ft.fx.pn.pivot(panel, "high").reindex_like(close)
    low = ft.fx.pn.pivot(panel, "low").reindex_like(close)
    ret = close.pct_change(fill_method=None)
    mkt = ret.mean(axis=1)
    resid = ret.sub(mkt, axis=0)
    M, vw = 4, 96
    sig = (resid.rolling(M, min_periods=M).sum() / resid.rolling(vw, min_periods=vw // 2).std())
    p = {"thr": thr, "sl": sl, "tgt": tgt, "hold": hold, "cooldown": cooldown,
         "be": be, "be_atr": 0.5, "warm": vw + 2}
    all_tr = []
    for s in close.columns:
        atr = _atr(high[s], low[s], close[s]).to_numpy()
        all_tr += _simulate_symbol(open_[s].to_numpy(), high[s].to_numpy(), low[s].to_numpy(),
                                   close[s].to_numpy(), atr, sig[s].to_numpy(), p)
    return pd.DataFrame(all_tr)


def _stats(tr):
    if not len(tr):
        return "no trades"
    win = (tr.r_pct > 0).mean()
    exp_r = tr.R.mean()
    gains = tr.loc[tr.r_pct > 0, "r_pct"].sum(); losses = -tr.loc[tr.r_pct < 0, "r_pct"].sum()
    pf = gains / losses if losses > 0 else np.inf
    return (f"n={len(tr):5d}  win={win:.3f}  mean={tr.r_pct.mean()*100:+.3f}%  "
            f"exp={exp_r:+.3f}R  PF={pf:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="15min")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--thr", type=float, default=2.0)
    args = ap.parse_args()
    print(f"selecting {args.n} liquid symbols + resampling to {args.tf} (IS only) ...")
    syms = ft._liquid_symbols(args.n)
    panel = ft.load_resampled(syms, args.tf)
    print(f"  panel: {panel['symbol'].nunique()} symbols x {panel['date'].nunique()} bars\n")
    print(f"=== intraday reversal fade (thr={args.thr}z, SL=1ATR, tgt=1.5ATR, hold=16 bars) ===")
    print("  SL->breakeven trigger:")
    for be in ("none", "atr", "prev", "two"):
        tr = run(panel, args.tf, be, thr=args.thr)
        print(f"    {be:5s}  {_stats(tr)}")


if __name__ == "__main__":
    main()
