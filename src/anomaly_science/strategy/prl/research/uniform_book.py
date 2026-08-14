"""Optimize the combined book for YEAR/MONTH UNIFORMITY, then size to target with leverage.

User criterion (2026-08-14): year-to-year jumps of 10x are unacceptable; the WOW result is
EVERY year +150-200% with uniform monthly income. That reframes selection: not max Sharpe
(runner-sizing lifted Sharpe but RE-concentrated 2025) but MAXIMIN -- maximize the WORST
year -- and low annual dispersion. Absolute level then comes from leverage on a uniform,
market-neutral, high-Sharpe book.

Sleeves: PRL (short-beta market-neutral), breakout-Donchian daily (long-beta trend),
breakout runner/flat & runner/regime (1h event, runner-sized). We scan sleeve weights,
score each combo by min-year / annual-dispersion / worst-month, find the maximin-uniform
weighting, then report the leverage L that lifts the WORST year to +150% and the resulting
maxDD (daily-rebalanced constant-leverage sim). IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.uniform_book --n 150
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.anatomy import run_book
from anomaly_science.strategy.prl.research.combined_book import breakout_sleeve
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.regime_runner_book import build_breakout_events, sleeve_returns


def annual(d):
    d = d.dropna()
    return {int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}


def summ(d):
    d = d.dropna()
    wk = d.resample("W").sum(); mo = d.resample("ME").sum()
    eq = (1 + d).cumprod(); dd = (eq / eq.cummax() - 1).min()
    sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    yr = annual(d)
    return dict(sh=sh, dd=dd, wk=float((wk > 0).mean()), mo=float((mo > 0).mean()),
                worst_mo=float(mo.min()), yr=yr, minyr=min(yr.values()), maxyr=max(yr.values()),
                yrstd=np.std(list(yr.values())))


def line(name, d):
    s = summ(d)
    ys = " ".join(f"{y}:{v*100:+.0f}%" for y, v in s["yr"].items())
    ratio = (s["maxyr"] / s["minyr"]) if s["minyr"] > 0 else float("inf")
    print(f"  {name:26s} Sh={s['sh']:+.2f} +wk={s['wk']*100:2.0f}% +mo={s['mo']*100:2.0f}% "
          f"mDD={s['dd']*100:+3.0f}% minYr={s['minyr']*100:+.0f}% ratio={ratio:>4.1f}x  [{ys}]")
    return s


def vp_weights(series_list):
    al = pd.concat(series_list, axis=1).dropna()
    inv = 1 / al.std(); return inv / inv.sum(), al


def lever_to_target(d, target=1.50):
    """Constant daily-rebalanced leverage L so the WORST year hits `target`; return L, levered stats."""
    d = d.dropna()
    best = None
    for L in np.arange(1.0, 12.01, 0.25):
        dl = (L * d)
        yr = annual(dl)
        if min(yr.values()) >= target:
            best = L; break
    L = best if best else 12.0
    dl = L * d
    return L, summ(dl)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()

    # 1h breakout event sleeves (runner-sized)
    idx, cols, close1h, ret1h, atr, ev, breadth_rank = build_breakout_events(args.n)
    bo_runner = sleeve_returns(idx, cols, ret1h, ev, breadth_rank, "runner", "flat")
    bo_runreg = sleeve_returns(idx, cols, ret1h, ev, breadth_rank, "runner", "regime")

    # daily sleeves
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); dclose, dum = m["close"], m["umask"]
    dret = dclose.pct_change(fill_method=None)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    dscore = oof.pivot(index="date", columns="symbol", values="score_linear").reindex_like(dclose)
    prl, _, _ = run_book(dclose, dret, dum, dscore, p, step=15, hyst=0.5)
    bo_don = breakout_sleeve(dclose, dret, dum)

    # normalize all sleeves to a common daily index (tz-naive dates)
    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl, bo_don, bo_runner, bo_runreg = map(norm, (prl, bo_don, bo_runner, bo_runreg))

    print("\n=== standalone sleeves ===")
    line("PRL market-neutral", prl)
    line("BO Donchian (daily)", bo_don)
    line("BO runner/flat (1h)", bo_runner)
    line("BO runner/regime (1h)", bo_runreg)

    # 2-sleeve weight sweep PRL + BO_donchian, score by MAXIMIN year
    print("\n=== PRL + BO-Donchian: weight sweep (maximin-year objective) ===")
    al = pd.concat([prl, bo_don], axis=1).dropna(); a, b = al.iloc[:, 0], al.iloc[:, 1]
    best = None
    for w in np.arange(0.0, 1.001, 0.05):
        d = w * a + (1 - w) * b; s = summ(d)
        if best is None or s["minyr"] > best[1]["minyr"]:
            best = (w, s, d)
    for w in (0.3, 0.4, 0.5, 0.6, 0.7, best[0]):
        line(f"PRL {w:.2f} / BO {1-w:.2f}", w * a + (1 - w) * b)
    print(f"  -> maximin weight w(PRL)={best[0]:.2f}  worst-year {best[1]['minyr']*100:+.0f}%")

    # candidate uniform books (unlevered)
    print("\n=== candidate combined books (unlevered) ===")
    wU, alU = vp_weights([prl, bo_don]); vp_prl_bo = (alU * wU).sum(axis=1)
    cand = {
        "PRL+BO 50/50": 0.5 * a + 0.5 * b,
        "PRL+BO maximin": best[2],
        "PRL+BO vol-parity": vp_prl_bo,
    }
    wR, alR = vp_weights([prl, bo_runreg]); cand["PRL+runner/regime vp"] = (alR * wR).sum(axis=1)
    for nm, d in cand.items():
        line(nm, d)

    # leverage the most-uniform book to +150% worst-year
    print("\n=== leverage to worst-year >= +150% (constant daily leverage) ===")
    for nm in ("PRL+BO 50/50", "PRL+BO maximin", "PRL+BO vol-parity"):
        d = cand[nm]; L, s = lever_to_target(d, 1.50)
        ys = " ".join(f"{y}:{v*100:+.0f}%" for y, v in s["yr"].items())
        print(f"  {nm:22s} L={L:4.1f}x -> Sh={s['sh']:+.2f} minYr={s['minyr']*100:+.0f}% "
              f"maxDD={s['dd']*100:+.0f}% worstMo={s['worst_mo']*100:+.0f}%  [{ys}]")


if __name__ == "__main__":
    main()
