"""Deepen and strengthen the anomalous-volume PUMP-FADE edge. Base population =
fade-pair events (ASIA->EU / EU->OVERLAP / LATE->ASIA) whose prior session was an
UP move (pump) with top-decile volume z. We then test whether each strengthening
lever sharpens the short: manipulation-isolation (idiosyncratic pump on a quiet
market), same-type volume baseline, cap, pump magnitude, new-high climax; plus a
weekly time-series and per-symbol recurrence for the champion cell.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
COST_BP = 8.0
FADE_PAIRS = ["ASIA->EU", "LATE->ASIA", "EU->OVERLAP"]


def short_stats(d, label):
    if len(d) < 150:
        print(f"    {label:<30} n={len(d):>5}  (too few)"); return None
    net = -d.cur_ret.mean() * 1e4 - COST_BP
    med = -d.cur_ret.median() * 1e4
    wk = -d.groupby("week").cur_ret.mean() * 1e4
    wkpos = (wk > 0).mean()
    print(f"    {label:<30} n={len(d):>5}  net={net:>+7.1f}b  med={med:>+7.1f}b  down%={d.cur_down.mean():.3f}  wk+={wkpos:.2f}")
    return net, wkpos


def dose(d, col, label, bins=8):
    d = d[np.isfinite(d[col])]
    if len(d) < 400:
        return
    q = pd.qcut(d[col], bins, labels=False, duplicates="drop")
    print(f"\n  dose: {label} -> SHORT net@8bp")
    for i, g in d.groupby(q):
        net = -g.cur_ret.mean() * 1e4 - COST_BP
        print(f"    q{int(i)} [{g[col].min():>7.2f}..{g[col].max():>7.2f}] n={len(g):>5} net={net:>+7.1f}b down%={g.cur_down.mean():.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/prev_vol.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    d = t[t.start_ts < DEV_END].copy()
    # base: fade pairs, prior pumped up, top-decile volume anomaly
    fade = d[d.pair.isin(FADE_PAIRS)].copy()
    thr = fade.vol_z.quantile(0.9)
    base = fade[(fade.vol_z >= thr) & (fade.p_up == 1)].copy()
    print(f"BASE = fade-pair UP-vol top-decile pumps: n={len(base):,}")
    short_stats(base, "BASE (no extra filter)")

    print("\n### 1. MANIPULATION-ISOLATION (idiosyncratic pump on a quiet market)")
    dose(base, "idio_ret", "coin move minus alt-market (idiosyncratic)")
    dose(base, "mkt_breadth_at_pump", "alt breadth during pump (low=isolated)")
    short_stats(base[base.idio_ret > base.idio_ret.median()], "idio_ret > median (isolated)")
    short_stats(base[base.mkt_breadth_at_pump < 0.4], "market breadth<0.4 (quiet mkt)")
    short_stats(base[(base.idio_ret > base.idio_ret.median()) & (base.mkt_breadth_at_pump < 0.45)], "isolated + quiet")

    print("\n### 1b. RELATIVE TO BTC in the anomalous period (trades & volume vs BTC)")
    dose(base, "reltc_z", "trade-count vs BTC z (retail crowding)")
    dose(base, "relbtc_z", "volume vs BTC z")
    short_stats(base[base.reltc_z > base.reltc_z.median()], "trades-vs-BTC z > median")

    print("\n### 1c. EXIT HORIZON (hold from entry to ...)")
    for col, lbl in [("ret_1", "next-session close (current)"), ("ret_2", "+2 sessions"),
                     ("ret_3", "+3 sessions (~US close for ASIA)"), ("ret_toanom", "next same-type-as-anomaly open (~1 day)")]:
        s = base[np.isfinite(base[col])]
        net = -s[col].mean() * 1e4 - COST_BP; med = -s[col].median() * 1e4
        wk = (-s.groupby("week")[col].mean() > 0).mean()
        print(f"    exit @ {lbl:<40} n={len(s):>5} net={net:>+7.1f}b med={med:>+7.1f}b wk+={wk:.2f}")

    print("\n### 2. SAME-TYPE volume baseline (this ASIA vs prior ASIAs)")
    dose(fade[fade.p_up == 1], "same_vol_z", "same-type vol_z (all up-pumps)")
    st_thr = fade.same_vol_z.quantile(0.9)
    short_stats(fade[(fade.same_vol_z >= st_thr) & (fade.p_up == 1)], "same-type top-decile up-pump")

    print("\n### 3. CAP (smaller = easier to manipulate?)")
    base["cap_tier"] = pd.qcut(base.cap, 3, labels=["low", "mid", "high"])
    for tier in ["low", "mid", "high"]:
        short_stats(base[base.cap_tier == tier], f"cap {tier}")

    print("\n### 4. PUMP MAGNITUDE (bigger pump -> bigger fade?)")
    dose(base, "p_ret", "prior pump return")
    dose(base, "p_close_pos", "prior close position (near high=FOMO top)")

    print("\n### 5. NEW-HIGH CLIMAX")
    short_stats(base[base.pump_new_high == 1], "pump made a NEW HIGH")
    short_stats(base[base.pump_new_high == 0], "pump did NOT make new high")

    print("\n### 6. CHAMPION cell = isolated + closed-top-third + new-high")
    champ = base[(base.p_close_pos > 0.6) & (base.pump_new_high == 1) & (base.idio_ret > base.idio_ret.median())]
    r = short_stats(champ, "CHAMPION")
    if r and len(champ) > 200:
        wk = (-champ.groupby("week").cur_ret.mean() * 1e4).sort_index()
        print(f"    weeks: {len(wk)} total, positive={int((wk>0).sum())}, worst 3 = {', '.join(f'{v:+.0f}' for v in wk.nsmallest(3))}bp, "
              f"best 3 = {', '.join(f'{v:+.0f}' for v in wk.nlargest(3))}bp")

    print("\n### 7. SYMBOL RECURRENCE (do specific coins get repeatedly pumped & faded?)")
    g = base.groupby("symbol").agg(n=("cur_ret", "size"), shnet=("cur_ret", lambda x: -x.mean() * 1e4 - COST_BP))
    g = g[g.n >= 15].sort_values("shnet", ascending=False)
    print(f"  {len(g)} coins with >=15 pump events. Top faders:",
          ", ".join(f"{s}({int(r.n)}:{r.shnet:+.0f}b)" for s, r in g.head(8).iterrows()))
    print("  worst (pump continues):", ", ".join(f"{s}({int(r.n)}:{r.shnet:+.0f}b)" for s, r in g.tail(5).iterrows()))
    print(f"  coin-level short net: mean={g.shnet.mean():+.1f}b, frac coins positive={ (g.shnet>0).mean():.2f}")


if __name__ == "__main__":
    main()
