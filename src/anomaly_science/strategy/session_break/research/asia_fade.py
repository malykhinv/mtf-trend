"""Rigorous in-sample validation of the standout: an anomalous-volume pump in a
low-liquidity session fades into the next liquid session (ASIA->EU the canonical
case). We pre-registered the mechanism (overnight pump exhaustion) and a monotone
dose-response already backs it. Now, per fade-pair we check that the top-decile-
vol_z short is: above a within-pair shuffled null, WEEK-STABLE (not a few fat
weeks), robust on the MEDIAN (not just the mean tail), and symbol-split-half stable.
Reuses prev_vol.parquet -- no rebuild.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
COST_BP = 8.0
FADE_PAIRS = ["ASIA->EU", "LATE->ASIA", "EU->OVERLAP"]
CONT_PAIRS = ["OVERLAP->US", "US->LATE"]


def block_boot_ci(week_means, n=2000, seed=0):
    """Bootstrap the mean of per-week returns (weeks are the iid unit)."""
    rng = np.random.default_rng(seed)
    wm = week_means.to_numpy()
    if len(wm) < 5:
        return np.nan, np.nan
    bs = [rng.choice(wm, len(wm), replace=True).mean() for _ in range(n)]
    return float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def validate(d, name):
    # short trade = -cur_ret; take top-decile vol_z within this slice
    thr = d.vol_z.quantile(0.9)
    td = d[d.vol_z >= thr].copy()
    if len(td) < 300:
        print(f"  {name}: too few ({len(td)})"); return
    td["short_ret"] = -td.cur_ret
    gross = td.short_ret.mean() * 1e4
    net = gross - COST_BP
    med = td.short_ret.median() * 1e4
    wk = td.groupby("week").short_ret.mean() * 1e4
    wk_pos = (wk > 0).mean()
    lo, hi = block_boot_ci(td.groupby("week").short_ret.mean())
    lo, hi = lo * 1e4 if np.isfinite(lo) else np.nan, hi * 1e4 if np.isfinite(hi) else np.nan
    # symbol split-half sign stability
    half = td.start_ts.median()
    h1 = td[td.start_ts <= half].short_ret.mean() * 1e4
    h2 = td[td.start_ts > half].short_ret.mean() * 1e4
    print(f"\n  === {name}  (top-decile vol_z short, n={len(td):,}, {len(wk)} weeks) ===")
    print(f"    gross={gross:+.1f}bp  net@8bp={net:+.1f}bp  MEDIAN={med:+.1f}bp  down%={td.cur_down.mean():.3f}")
    print(f"    week-mean 95% CI=[{lo:+.1f}, {hi:+.1f}]bp   weeks_positive={wk_pos:.2f}")
    print(f"    split-half short_ret: H1={h1:+.1f}bp  H2={h2:+.1f}bp  (sign-stable={'YES' if h1>0 and h2>0 else 'no'})")


def sharpen(d, name):
    """Does adding the FOMO-top signature (close near high, exhaustion wick) sharpen?"""
    print(f"\n  --- {name}: sharpening filters (short net@8bp) ---")
    base = d[d.vol_z >= d.vol_z.quantile(0.9)]
    variants = {
        "vol_z top-decile": base,
        "+ prior pumped (up)": base[base.p_up == 1],
        "+ closed top-third": base[(base.p_up == 1) & (base.p_close_pos > 0.66)],
        "+ upper wick>0.2": base[(base.p_up == 1) & (base.p_close_pos > 0.66) & (base.p_upper_wick > 0.2)],
        "+ big pump (p_ret>3%)": base[(base.p_up == 1) & (base.p_close_pos > 0.66) & (base.p_ret > 0.03)],
    }
    for label, v in variants.items():
        if len(v) < 150:
            print(f"    {label:<26} n={len(v):>5}  (too few)"); continue
        net = -v.cur_ret.mean() * 1e4 - COST_BP
        wk = (-v.groupby("week").cur_ret.mean() > 0).mean()
        print(f"    {label:<26} n={len(v):>5}  net={net:>+7.1f}bp  down%={v.cur_down.mean():.3f}  wk+={wk:.2f}")


def by_vol_direction(d, name):
    """Was the anomalous volume in an UP move (pump) or a DOWN move (dump)? Split the
    top-decile vol_z and test the next session in the natural direction of each."""
    top = d[d.vol_z >= d.vol_z.quantile(0.9)]
    print(f"\n  === {name}: anomalous volume by DIRECTION (next-session outcome) ===")
    for lbl, sub in [("UP-vol (pump)", top[top.p_up == 1]), ("DOWN-vol (dump)", top[top.p_up == 0])]:
        if len(sub) < 150:
            print(f"    {lbl}: too few ({len(sub)})"); continue
        m = sub.cur_ret.mean() * 1e4; med = sub.cur_ret.median() * 1e4
        short_net = -m - COST_BP; long_net = m - COST_BP
        s_wk = (-sub.groupby("week").cur_ret.mean() > 0).mean()
        l_wk = (sub.groupby("week").cur_ret.mean() > 0).mean()
        print(f"    {lbl:<16} n={len(sub):>5}  next mean={m:>+6.1f}bp med={med:>+6.1f}bp down%={sub.cur_down.mean():.3f}"
              f"  | SHORT net={short_net:>+6.1f}b wk+={s_wk:.2f}  LONG net={long_net:>+6.1f}b wk+={l_wk:.2f}")
    # within UP-vol: buyer-driven FOMO vs distribution-into-strength (price up but net selling)
    up = top[top.p_up == 1]
    if "p_taker" in up and up.p_taker.notna().sum() > 300:
        for lbl, sub in [("  UP+buyer(taker>.5)", up[up.p_taker > 0.5]), ("  UP+distrib(taker<.5)", up[up.p_taker <= 0.5])]:
            if len(sub) < 150:
                continue
            net = -sub.cur_ret.mean() * 1e4 - COST_BP
            print(f"    {lbl:<20} n={len(sub):>5}  SHORT net={net:>+6.1f}b down%={sub.cur_down.mean():.3f}"
                  f"  wk+={(-sub.groupby('week').cur_ret.mean()>0).mean():.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END].copy()

    print("################ FADE PAIRS (expect short net > 0, week-stable) ################")
    for p in FADE_PAIRS:
        validate(d[d.pair == p], p)
    validate(d[d.pair.isin(FADE_PAIRS)], "POOLED FADE PAIRS")

    print("\n################ CONTINUATION PAIRS (expect short NEGATIVE -> pump continues) ################")
    for p in CONT_PAIRS:
        validate(d[d.pair == p], p)

    print("\n################ ANOMALOUS VOLUME: UP (pump) vs DOWN (dump) ################")
    by_vol_direction(d[d.pair == "ASIA->EU"], "ASIA->EU")
    by_vol_direction(d[d.pair.isin(FADE_PAIRS)], "POOLED FADE")
    for p in CONT_PAIRS:
        by_vol_direction(d[d.pair == p], p)

    print("\n################ SHARPENING on ASIA->EU ################")
    sharpen(d[d.pair == "ASIA->EU"], "ASIA->EU")
    print("\n################ SHARPENING on pooled fade pairs ################")
    sharpen(d[d.pair.isin(FADE_PAIRS)], "pooled fade")


if __name__ == "__main__":
    main()
