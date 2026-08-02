"""Aggregate the fade-short backtest into an honest verdict.

Mean/median net R, win rate, total R, and the anti-lottery week check per cell.
Sliced by entry x stop, by stretch bucket (fade only the over-extended?), and by
tier -- to see whether ANY selection is both positive-mean and week-stable.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def _pos_weeks(g):
    wk = g.groupby("week")["net_r"].sum()
    return float((wk > 0).mean()) if len(wk) else np.nan


def summarise(t, by):
    rows = []
    for key, g in t.groupby(list(by)):
        rows.append({
            **dict(zip(by, key if isinstance(key, tuple) else (key,))),
            "n": len(g), "mean_r": g["net_r"].mean(), "median_r": g["net_r"].median(),
            "win": (g["net_r"] > 0).mean(), "sum_r": g["net_r"].sum(),
            "pos_weeks": _pos_weeks(g), "n_weeks": g["week"].nunique(),
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=Path, default=Path(".output/results/session_break/fade_trades.parquet"))
    ap.add_argument("--min-score", type=float, default=0.0,
                    help="restrict to reverter score_pct >= this (e.g. 0.9 top decile)")
    args = ap.parse_args()
    t = pd.read_parquet(args.trades)
    if args.min_score > 0 and "score_pct" in t.columns:
        t = t[t["score_pct"] >= args.min_score]
        print(f"[selection] reverter score_pct >= {args.min_score}: {len(t):,} trades")
    t["stretch_bkt"] = pd.cut(t["stretch"], [-np.inf, 2, 4, 6, np.inf],
                              labels=["<2", "2-4", "4-6", ">6"])
    fmt = lambda x: f"{x:.3f}"
    print(f"fade trades: {len(t):,}")

    print("\n=== by entry x stop (pooled) ===")
    print(summarise(t, ["entry", "stop"]).sort_values("mean_r", ascending=False)
          .to_string(index=False, float_format=fmt))

    print("\n=== by stretch bucket (entry=stall, stop=s10) ===")
    sub = t[(t.entry == "stall") & (t.stop == "s10")]
    print(summarise(sub, ["stretch_bkt"]).to_string(index=False, float_format=fmt))

    print("\n=== best-looking cell by tier (entry=stall,stop=s10,stretch>4) ===")
    sub2 = t[(t.entry == "stall") & (t.stop == "s10") & (t.stretch >= 4)]
    print(summarise(sub2, ["variant", "tier"]).sort_values("mean_r", ascending=False)
          .to_string(index=False, float_format=fmt))

    print("\n=== positive & week-stable cells (mean_r>0, pos_weeks>=0.55, n>=200) ===")
    full = summarise(t, ["variant", "tier", "entry", "stop"])
    good = full[(full.mean_r > 0) & (full.pos_weeks >= 0.55) & (full.n >= 200)]
    print(good.sort_values("mean_r", ascending=False).to_string(index=False, float_format=fmt)
          if len(good) else "  NONE")

    print("\n=== + stretch>=4 filter, positive & week-stable ===")
    ts = t[t.stretch >= 4]
    fulls = summarise(ts, ["variant", "tier", "entry", "stop"])
    goods = fulls[(fulls.mean_r > 0) & (fulls.pos_weeks >= 0.55) & (fulls.n >= 200)]
    print(goods.sort_values("mean_r", ascending=False).to_string(index=False, float_format=fmt)
          if len(goods) else "  NONE")


if __name__ == "__main__":
    main()
