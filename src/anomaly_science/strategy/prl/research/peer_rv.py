"""PRL-PEER relative value: within-basket convergence (the REAL use of correlation
baskets, user). A coin that temporarily diverges from the peers it normally co-moves
with tends to converge back -- a reversal, not momentum. We test:

  signal  = recent peer-residual divergence (eps - peer_LOO, cumulated)
  target  = FUTURE peer-residual return (relative to the basket)
  edge    = NEGATIVE IC (divergence up -> converges down), and a cluster-neutral book
            that longs the under-diverged and shorts the over-diverged within each basket.

IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.peer_rv --k 12
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import ic as icmod
from anomaly_science.strategy.prl.research.peer import build_clusters, peer_loo
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.run_coarse import _warmup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=12)
    args = ap.parse_args()
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    eps, close, um = m["eps"], m["close"], m["umask"]
    ret = close.pct_change(fill_method=None)

    print(f"clusters k={args.k} ...")
    assign, diag = build_clusters(eps, um, k=args.k)
    peer = peer_loo(eps, assign)
    pr = (eps - peer).where(um)                       # daily peer-residual (divergence rate)

    warmup = _warmup(p)
    ev = eps.index[warmup:]
    print(f"  median cluster size={diag['median_cluster_size']:.1f}\n")
    print("=== within-basket: recent divergence -> future peer-residual (NEG IC = convergence) ===")
    for lb in (3, 5, 10):
        sig = pr.rolling(lb, min_periods=max(2, lb // 2)).sum().shift(p.skip).where(um)
        for fwd in (5, 10):
            lab = pr.rolling(fwd, min_periods=max(2, fwd // 2)).sum().shift(-fwd)
            ic = icmod.daily_ic(sig, lab, p, ev)
            s = icmod.summarize_ic(ic)
            ic.index = pd.to_datetime(ic.index)
            yr = {y: g.mean() for y, g in ic.groupby(ic.index.year)}
            ys = " ".join(f"{y}:{v:+.3f}" for y, v in yr.items())
            print(f"  lb={lb:2d} fwd={fwd:2d}  IC={s['ic_mean']:+.4f} (t={s['t_stat']:+.1f})  [{ys}]")

    # cluster-neutral convergence book: within each basket, weight ~ -recent divergence
    print("\n=== cluster-neutral convergence book (short over-diverged / long under-diverged) ===")
    def book(lb, hold, cost_mult=1.0):
        sig = pr.rolling(lb, min_periods=max(2, lb // 2)).sum().shift(p.skip)
        idx = close.index; daily = pd.Series(0.0, index=idx); prev = pd.Series(dtype=float)
        side = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * cost_mult / 1e4
        for ri in range(warmup, len(idx) - hold - 1, hold):
            s = sig.iloc[ri].where(um.iloc[ri]).dropna()
            c = assign.iloc[ri].reindex(s.index)
            df = pd.DataFrame({"s": s, "c": c}).dropna()
            if len(df) < 20:
                continue
            # demean divergence WITHIN each cluster, short positive divergence (converge)
            df["w"] = -(df["s"] - df.groupby("c")["s"].transform("mean"))
            w = df["w"]
            if w.abs().sum() == 0:
                continue
            w = w / w.abs().sum()
            if len(prev):
                w = 0.5 * w + 0.5 * prev.reindex(w.index).fillna(0)
                w = w - w.mean(); w = w / w.abs().sum()
            turn = float((w.subtract(prev, fill_value=0.0)).abs().sum())
            for dd in range(ri + 1, min(ri + 1 + hold, len(idx))):
                daily.iloc[dd] += float((w * ret.iloc[dd].reindex(w.index)).sum(skipna=True))
            daily.iloc[ri + 1] -= turn * side
            prev = w
        d = daily.loc[daily.ne(0).cumsum() > 0]
        wk = d.resample("W").sum(); eq = (1 + d).cumprod()
        yr = {y: (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
        return eq.iloc[-1] ** (252 / len(d)) - 1, d.mean() / d.std() * np.sqrt(252), float((wk > 0).mean()), yr
    print(f"  {'lb/hold':>10} {'CAGR':>7} {'Sharpe':>7} {'+weeks':>7}  by-year")
    for lb, hold in ((3, 5), (5, 5), (5, 10), (10, 10)):
        c, sh, wp, yr = book(lb, hold)
        ys = " ".join(f"{y}:{v*100:+.0f}%" for y, v in yr.items())
        print(f"  lb{lb} h{hold:<5} {c*100:+6.0f}% {sh:7.2f} {wp*100:6.0f}%  [{ys}]")


if __name__ == "__main__":
    main()
