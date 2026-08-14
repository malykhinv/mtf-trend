"""NEW event strategy (Stage 1): 1d-high breakout -> 1h hold-vs-fail -> direction.

A coin breaks its trailing 20d high (detected on 1h). We watch on 1h whether it
CONSOLIDATES above the level (held) or falls back UNDER it (failed), T hours later.
Thesis: held -> continuation (long), failed -> reversal (short). Stage 1 only asks:
is there a base directional edge, before any features/entries?

Discipline (hard-won): per-year breakdown, symbol/period concentration, and a BLIND
baseline (forward return from random bars) -- an event edge must beat blind.

Data: klines_1h (breakout/decision/forward) + daily highs from it. Market-relative to
BTC over each event's actual window. IS only (<2026-01), OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.breakout_probe --T 4 --H 24 --lvl 20
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h, IS_END


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--T", type=int, default=4, help="hours after breakout to judge hold/fail")
    ap.add_argument("--H", type=int, default=24, help="forward horizon (hours) after decision")
    ap.add_argument("--lvl", type=int, default=20, help="trailing N-day high as the level")
    args = ap.parse_args()
    T, H, LVL = args.T, args.H, args.lvl

    print(f"loading 1h (top-{args.n} liquid) ...")
    panel = load_1h(_liquid(args.n))
    close_w = panel.pivot(index="date", columns="symbol", values="close")
    btc = close_w["BTCUSDT"] if "BTCUSDT" in close_w else close_w.median(axis=1)
    btc_fwd = (btc.shift(-(T + H)) / btc.shift(-T) - 1.0)  # BTC over the [decision, decision+H] window

    rows = []
    for sym, g in panel.groupby("symbol"):
        g = g.sort_values("date").set_index("date")
        c, h = g["close"], g["high"]
        qv = g["quote_volume"]
        dhigh = h.resample("1D").max()
        level_d = dhigh.rolling(LVL, min_periods=LVL // 2).max().shift(1)  # prior N-day high, as-of yesterday
        level = level_d.reindex(c.index, method="ffill")
        age_d = (dhigh >= dhigh.rolling(LVL, min_periods=LVL // 2).max()).astype(int)  # placeholder
        # breakout = close crosses above the level
        brk = (c > level) & (c.shift(1) <= level.shift(1)) & level.notna()
        idx = np.where(brk.to_numpy())[0]
        cval = c.to_numpy(); lvlval = level.to_numpy()
        n = len(c)
        bf = btc_fwd.reindex(c.index).to_numpy()
        vsurge = (qv / qv.rolling(168, min_periods=48).mean()).to_numpy()  # volume inflow at breakout
        for i in idx:
            dj = i + T
            fj = i + T + H
            if fj >= n or not (cval[dj] > 0):
                continue
            held = cval[dj] >= lvlval[i]         # still above the broken level after T hours
            fwd = cval[fj] / cval[dj] - 1.0
            rel = fwd - (bf[dj] if np.isfinite(bf[dj]) else 0.0)
            rows.append({"symbol": sym, "date": c.index[i], "held": bool(held),
                         "fwd_rel": rel, "vsurge": vsurge[i]})
    tr = pd.DataFrame(rows)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    print(f"breakout events: {len(tr)}  held={int(tr.held.sum())}  failed={int((~tr.held).sum())}\n")

    # blind baseline: forward return from ALL bars (random entry), market-relative
    blind = []
    for sym, g in panel.groupby("symbol"):
        g = g.sort_values("date").set_index("date"); c = g["close"]
        f = (c.shift(-(T + H)) / c.shift(-T) - 1.0) - btc_fwd.reindex(c.index)
        blind.append(f.dropna())
    blind = pd.concat(blind)
    print(f"[BLIND baseline] mean fwd_rel={blind.mean()*100:+.3f}%  median={blind.median()*100:+.3f}%\n")

    for grp, name in ((tr[tr.held], "HELD (long thesis)"), (tr[~tr.held], "FAILED (short thesis)")):
        if not len(grp):
            continue
        wr = float((grp.fwd_rel > 0).mean())
        yr = {y: gy.fwd_rel.mean() for y, gy in grp.groupby("year")}
        ys = " ".join(f"{y}:{v*100:+.2f}%" for y, v in yr.items())
        bysym = grp.groupby("symbol")["fwd_rel"].sum().sort_values(ascending=False)
        pos = bysym[bysym > 0].sum() or 1.0
        top5 = bysym.head(5).sum() / pos
        print(f"=== {name}: n={len(grp)} ===")
        print(f"  fwd_rel mean={grp.fwd_rel.mean()*100:+.3f}%  median={grp.fwd_rel.median()*100:+.3f}%  win={wr:.3f}")
        print(f"  by-year [{ys}]")
        print(f"  top-5 symbol share of positive PnL={top5:.2f}")
    # the discriminator: does held vs failed separate forward direction?
    if len(tr[tr.held]) and len(tr[~tr.held]):
        gap = tr[tr.held].fwd_rel.mean() - tr[~tr.held].fwd_rel.mean()
        print(f"\n  HELD minus FAILED forward gap = {gap*100:+.3f}%  (positive => hold/fail predicts direction)")

    # Stage-2 probe: can VOLUME INFLOW select the runners among HELD breakouts?
    print("\n=== HELD breakouts split by volume inflow (vsurge) tercile ===")
    hg = tr[tr.held].dropna(subset=["vsurge"]).copy()
    hg["q"] = pd.qcut(hg["vsurge"], 3, labels=["low", "mid", "high"], duplicates="drop")
    for q, g in hg.groupby("q", observed=True):
        wr = float((g.fwd_rel > 0).mean())
        print(f"  vsurge {q:4s}  n={len(g):5d}  mean={g.fwd_rel.mean()*100:+.3f}%  "
              f"median={g.fwd_rel.median()*100:+.3f}%  win={wr:.3f}")
    print("  (if high-vsurge held median>0 & win>0.5 -> metrics can rescue the lottery)")


if __name__ == "__main__":
    main()
