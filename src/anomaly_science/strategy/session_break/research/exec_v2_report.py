"""Aggregate the long exec-v2 grid (N*ATR stop x exit mode x TP) honestly."""

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
    ap.add_argument("--trades", type=Path, default=Path(".output/results/session_break/trades_v2.parquet"))
    ap.add_argument("--min-score", type=float, default=0.0)
    args = ap.parse_args()
    t = pd.read_parquet(args.trades)
    if args.min_score > 0 and "score_pct" in t.columns:
        t = t[t["score_pct"] >= args.min_score]
        print(f"[selection] score_pct>={args.min_score}: {len(t):,} trades")
    fmt = lambda x: f"{x:.3f}"
    print(f"trades: {len(t):,}")
    print("\n=== by stop_n x mode x tp_m (pooled) ===")
    print(summarise(t, ["stop_n", "mode", "tp_m"]).sort_values("mean_r", ascending=False)
          .to_string(index=False, float_format=fmt))
    print("\n=== best mode (partial, tp_m=4) by tier x stop_n ===")
    sub = t[(t["mode"] == "partial") & (t["tp_m"] == 4.0)]
    print(summarise(sub, ["tier", "stop_n"]).sort_values("mean_r", ascending=False)
          .to_string(index=False, float_format=fmt))
    print("\n=== positive & week-stable cells (mean_r>0, pos_weeks>=0.55, n>=200) ===")
    full = summarise(t, ["variant", "tier", "stop_n", "mode", "tp_m"])
    good = full[(full.mean_r > 0) & (full.pos_weeks >= 0.55) & (full.n >= 200)]
    print(good.sort_values("mean_r", ascending=False).to_string(index=False, float_format=fmt)
          if len(good) else "  NONE")


if __name__ == "__main__":
    main()
