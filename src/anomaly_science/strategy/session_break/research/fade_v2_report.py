"""Honest fade P&L: GROSS edge and the BREAKEVEN cost.

The v2 short engine stores sign-correct GROSS R (-1 stop floor) plus each trade's
risk fraction. Net R at a given round-trip cost c (as a price fraction) is:

    net_r = gross_r - c / risk_frac        (cost is c of notional, risk is risk_frac
                                            of notional, so cost in R = c / risk_frac)

We report gross, net at taker (30 bps) and maker (2 bps) costs, and the breakeven
round-trip cost that drives mean net R to zero -- the real answer to "under what
circumstances is the fade profitable".
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def net_at(g, c):
    return (g["gross_r"] - c / g["risk_frac"]).clip(-10, 10)


def _stats(g, c):
    nr = net_at(g, c)
    wk = g.assign(x=nr).groupby("week")["x"].sum()
    return pd.Series({
        "n": len(g), "gross": g["gross_r"].clip(-10, 10).mean(),
        "net": nr.mean(), "median": nr.median(), "win": (nr > 0).mean(),
        "pos_weeks": (wk > 0).mean(),
        "breakeven_bps": 1e4 * (g["gross_r"] / (1.0 / g["risk_frac"])).replace([np.inf, -np.inf], np.nan).mean(),
    })


def summarise(t, by, c):
    return t.groupby(list(by)).apply(lambda g: _stats(g, c)).reset_index()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=Path, default=Path(".output/results/session_break/fade_v2.parquet"))
    ap.add_argument("--cost", type=float, default=0.0030, help="round-trip cost as price fraction (30bps=0.003)")
    ap.add_argument("--min-stretch", type=float, default=0.0)
    args = ap.parse_args()
    t = pd.read_parquet(args.trades)
    if "gross_r" not in t.columns:
        print("trades file has no gross_r -- rebuild with the fixed engine"); return
    if args.min_stretch > 0:
        t = t[t.stretch >= args.min_stretch]
    t = t[t.risk_frac > 0]
    fmt = lambda x: f"{x:.3f}"
    print(f"trades: {len(t):,}   cost={args.cost*1e4:.0f}bps round trip")

    print("\n=== GROSS mean R by mode x stop_n (median risk_frac shown as %) ===")
    g = t.groupby(["mode", "stop_n"]).agg(
        n=("gross_r", "size"), gross=("gross_r", lambda s: s.clip(-10, 10).mean()),
        risk_pct=("risk_frac", lambda s: 100 * s.median())).reset_index()
    print(g.sort_values("gross", ascending=False).to_string(index=False, float_format=fmt))

    for c, lbl in [(0.0, "GROSS (0bps)"), (0.0002, "maker 2bps"), (args.cost, f"taker {args.cost*1e4:.0f}bps")]:
        print(f"\n=== NET at {lbl}: best cells (mode=vwap) ===")
        sub = summarise(t[t["mode"] == "vwap"], ["session", "tier", "stop_n"], c)
        good = sub[(sub.net > 0) & (sub.pos_weeks >= 0.6) & (sub.n >= 300)]
        print(good.sort_values("net", ascending=False).head(12).to_string(index=False, float_format=fmt)
              if len(good) else "  NONE positive & week-stable")

    print("\n=== BREAKEVEN round-trip cost (bps) by stop_n (vwap) -- higher = more robust ===")
    be = t[t["mode"] == "vwap"].groupby("stop_n").apply(
        lambda g: 1e4 * (g["gross_r"] * g["risk_frac"]).mean() / 1.0).reset_index(name="mean_gross_bps_of_notional")
    # breakeven cost where mean(gross_r - c/risk)=0  => c* = mean(gross_r) / mean(1/risk)
    for n, g in t[t["mode"] == "vwap"].groupby("stop_n"):
        cstar = g["gross_r"].mean() / (1.0 / g["risk_frac"]).mean()
        print(f"  stop_n={n}: gross_mean_R={g['gross_r'].clip(-10,10).mean():+.3f}  median_risk={100*g['risk_frac'].median():.2f}%  breakeven_cost={cstar*1e4:.1f}bps round-trip")


if __name__ == "__main__":
    main()
