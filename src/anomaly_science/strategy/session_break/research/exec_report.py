"""Aggregate the execution grid into an honest per-cell verdict.

For each (variant x tier x entry x stop): trade count, mean/median net R, win
rate, total R, and -- the anti-lottery check -- the fraction of ISO weeks whose
summed R is positive. A cell is only interesting if mean R > 0 AND most weeks are
positive (steady, not one lucky week).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def _weekly_positive_fraction(g: pd.DataFrame) -> float:
    wk = g.groupby("week")["net_r"].sum()
    return float((wk > 0).mean()) if len(wk) else np.nan


def summarise(trades: pd.DataFrame, by=("variant", "tier", "entry", "stop")) -> pd.DataFrame:
    rows = []
    for key, g in trades.groupby(list(by)):
        rows.append({
            **dict(zip(by, key if isinstance(key, tuple) else (key,))),
            "n": len(g),
            "mean_r": g["net_r"].mean(),
            "median_r": g["net_r"].median(),
            "win": (g["net_r"] > 0).mean(),
            "sum_r": g["net_r"].sum(),
            "pos_weeks": _weekly_positive_fraction(g),
            "n_weeks": g["week"].nunique(),
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=Path, default=Path(".output/results/session_break/trades.parquet"))
    ap.add_argument("--min-pct", type=float, default=0.0,
                    help="restrict to score_pct >= this (e.g. 0.9 for top decile)")
    args = ap.parse_args()

    t = pd.read_parquet(args.trades)
    if args.min_pct > 0:
        t = t[t["score_pct"] >= args.min_pct]
    print(f"trades: {len(t):,}  (score_pct>={args.min_pct})")

    fmt = lambda x: f"{x:.3f}"
    print("\n=== by entry x stop (pooled over variant/tier) ===")
    s = summarise(t, by=("entry", "stop")).sort_values("mean_r", ascending=False)
    print(s.to_string(index=False, float_format=fmt))

    print("\n=== best stop (struct60) by variant x tier x entry ===")
    s2 = summarise(t[t["stop"] == "struct60"], by=("variant", "tier", "entry"))
    print(s2.sort_values("mean_r", ascending=False).to_string(index=False, float_format=fmt))

    print("\n=== positive cells (mean_r>0 AND pos_weeks>=0.55) ===")
    full = summarise(t)
    good = full[(full["mean_r"] > 0) & (full["pos_weeks"] >= 0.55) & (full["n"] >= 100)]
    if len(good):
        print(good.sort_values("mean_r", ascending=False).to_string(index=False, float_format=fmt))
    else:
        print("  NONE -- no cell is both positive-mean and week-stable.")


if __name__ == "__main__":
    main()
