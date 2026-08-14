"""Causal regime-conditional allocation to even the years -- with the overfitting guard.

User: allocate by circumstances, not statically. LEGIT version: causal, economically-FIXED
mapping (not fit to the 3-year outcome) -- more breakout when breadth/risk-on is high, more PRL
when breadth is low (bear/dispersion) -- on top of vol-parity, then a vol-target overlay. We
compare year-uniformity + Sharpe vs STATIC baselines (50/50, vol-parity), and SHUFFLE the regime
signal (permuted breadth -> the tilt is random -> any uniformity/Sharpe gain must vanish; if the
shuffled version does as well, the "smart" allocation is curve-fitting 3 points, not a real rule).
The only honest out-of-regime test is 2022 (downloading). IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.regime_allocator
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.anatomy import run_book
from anomaly_science.strategy.prl.research.combined_book import breakout_sleeve
from anomaly_science.strategy.prl.research.policy import PRIMARY


def stat(d):
    d = d.dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    cagr = eq.iloc[-1] ** (252 / len(d)) - 1
    sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    yr = {int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
    mn = min(yr.values()); mx = max(yr.values())
    return dict(sh=sh, dd=dd, cagr=cagr, yr=yr, minyr=mn, ratio=(mx / mn if mn > 0 else np.inf))


def line(nm, d):
    s = stat(d); ys = " ".join(f"{v*100:+.0f}%" for v in s["yr"].values())
    print(f"  {nm:26s} Sh={s['sh']:+.2f} CAGR={s['cagr']*100:+3.0f}% maxDD={s['dd']*100:+3.0f}% "
          f"minYr={s['minyr']*100:+.0f}% ratio={s['ratio']:>4.1f}x  [{ys}]")


def vt(d, lb=20, cap=3.0):
    v = d.rolling(lb, min_periods=lb // 2).std().shift(1); sc = (1 / v.replace(0, np.nan)); sc = (sc / sc.mean()).clip(upper=cap).fillna(1.0)
    return d * sc


def main():
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um = m["close"], m["umask"]; ret = close.pct_change(fill_method=None)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    score = oof.assign(date=pd.to_datetime(oof["date"], utc=True)).pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)
    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)
    A = pd.concat([prl.rename("PRL"), bo.rename("BO")], axis=1).dropna()

    # causal regime: breadth (risk-on), expanding percentile (known day-before)
    breadth = (close >= close.rolling(20, min_periods=10).max().shift(1)).mean(axis=1)
    br_rank = breadth.expanding(min_periods=60).apply(lambda x: (x.iloc[-1] >= x).mean(), raw=False).shift(1).reindex(A.index).fillna(0.5)

    def dyn_weights(brk_rank):
        # base vol-parity (inverse trailing vol) x economic tilt (BO up in risk-on, PRL up in risk-off)
        inv = 1.0 / A.rolling(60, min_periods=20).std().shift(1)
        wv = inv.div(inv.sum(axis=1), axis=0).fillna(0.5)
        tilt_bo = 0.5 + brk_rank; tilt_prl = 0.5 + (1 - brk_rank)
        w_bo = wv["BO"] * tilt_bo; w_prl = wv["PRL"] * tilt_prl
        s = w_bo + w_prl
        return (w_prl / s) * A["PRL"] + (w_bo / s) * A["BO"]

    print("=== static baselines ===")
    line("PRL alone", A["PRL"])
    line("BO alone", A["BO"])
    line("50/50", 0.5 * A["PRL"] + 0.5 * A["BO"])
    inv = 1.0 / A.std(); line("vol-parity (static)", (A * (inv / inv.sum())).sum(axis=1))
    line("vol-parity + VT", vt((A * (inv / inv.sum())).sum(axis=1)))

    print("\n=== causal regime-conditional (breadth-tilted, economic-fixed mapping) ===")
    dyn = dyn_weights(br_rank)
    line("regime-tilt", dyn)
    line("regime-tilt + VT", vt(dyn))

    print("\n=== SHUFFLE control (permuted breadth -> tilt is random) ===")
    rng = np.random.default_rng(0)
    for k in range(3):
        sh = pd.Series(rng.permutation(br_rank.values), index=br_rank.index)
        line(f"shuffled-regime #{k}", vt(dyn_weights(sh)))
    print("\n  -> if regime-tilt+VT does NOT beat vol-parity+VT and the shuffles, the tilt is curve-fit.")
    print("  -> real out-of-regime test = 2022 bear (downloading).")


if __name__ == "__main__":
    main()
