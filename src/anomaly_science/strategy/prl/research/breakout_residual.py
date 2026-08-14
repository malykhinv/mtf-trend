"""Expansion: RESIDUAL / ISOLATED breakouts (the year-stable winning kind).

Stage 2 found isolated breakouts (basket -0.12*, low breadth) beat broad ones. Two
expansions of the breakout definition:
  A. RESIDUAL breakout: the coin breaks its 20d high in RELATIVE price (close/market
     index) -- genuine relative-strength breakout, not a market-wide move.
  B. ABSOLUTE breakout filtered by ISOLATION (low market breadth at the break).
For each: HELD forward outcome (raw & residual), win / median / runner, per-year.
Causal, IS only. Run: python -m anomaly_science.strategy.prl.research.breakout_residual --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

T, H, LVL = 4, 24, 20


def _events(price, level, C, dj_ok):
    above = price >= level
    brk = above & (~above.shift(1).fillna(False)) & level.notna()
    return brk.to_numpy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    close = panel.pivot(index="date", columns="symbol", values="close")
    high = panel.pivot(index="date", columns="symbol", values="high")
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None)
    mkt = (1 + ret.mean(axis=1).fillna(0)).cumprod()                    # EW market index
    resid = close.div(mkt, axis=0)                                       # relative price
    rhigh = high.div(mkt, axis=0)

    def level_of(hmat):
        dh = hmat.resample("1D").max()
        return dh.rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")

    lvl_abs = level_of(high)
    lvl_res = level_of(rhigh)
    above_abs = close >= lvl_abs
    breadth = above_abs.mean(axis=1)                                     # market breadth (isolation proxy)

    brk_abs = _events(close, lvl_abs, None, None)
    brk_res = _events(resid, lvl_res, None, None)
    Cn, Rn, La, Lr = close.to_numpy(), resid.to_numpy(), lvl_abs.to_numpy(), lvl_res.to_numpy()
    bre = breadth.to_numpy()

    def collect(mask, price_n, lvl_n, use_resid_fwd):
        rows = []
        for si in range(len(cols)):
            for i in np.where(mask[:, si])[0]:
                dj, fj = i + T, i + T + H
                if fj >= len(idx) or not (Cn[dj, si] > 0):
                    continue
                held = price_n[dj, si] >= lvl_n[i, si]
                if use_resid_fwd:
                    fwd = Rn[fj, si] / Rn[dj, si] - 1.0 if Rn[dj, si] > 0 else np.nan
                else:
                    fwd = Cn[fj, si] / Cn[dj, si] - 1.0
                rows.append({"date": idx[i], "held": bool(held), "fwd": fwd, "breadth": bre[i]})
        d = pd.DataFrame(rows).dropna(subset=["fwd"])
        d["year"] = pd.to_datetime(d.date).dt.year
        return d

    def rep(d, tag):
        h = d[d.held]
        yr = " ".join(f"{y}:{(gy.fwd>0).mean():.2f}" for y, gy in h.groupby("year"))
        print(f"  {tag:26s} n={len(h):5d} win={(h.fwd>0).mean():.3f} med={h.fwd.median()*100:+.2f}% "
              f"mean={h.fwd.mean()*100:+.2f}% runner={(h.fwd>0.05).mean():.3f} win/yr[{yr}]")

    print("\n=== A. RESIDUAL breakout (coin breaks its RELATIVE 20d high) ===")
    dr = collect(brk_res, Rn, Lr, use_resid_fwd=True)
    rep(dr, "residual-fwd (held)")
    dr2 = collect(brk_res, Rn, Lr, use_resid_fwd=False)
    rep(dr2, "raw-fwd (held)")

    print("\n=== B. ABSOLUTE breakout split by ISOLATION (market breadth) ===")
    da = collect(brk_abs, Cn, La, use_resid_fwd=False)
    da_h = da[da.held]
    thr = da_h.breadth.median()
    rep(da_h[da_h.breadth <= thr], "ISOLATED (low breadth)")
    rep(da_h[da_h.breadth > thr], "BROAD (high breadth)")
    # residual forward for isolated absolute breakouts
    dar = collect(brk_abs, Cn, La, use_resid_fwd=True)
    dar_h = dar[dar.held]
    rep(dar_h[dar_h.breadth <= dar_h.breadth.median()], "ISOLATED resid-fwd")


if __name__ == "__main__":
    main()
