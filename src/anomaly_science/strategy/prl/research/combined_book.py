"""Regime-complementary combined book: PRL market-neutral + breakout-long trend.

PRL (rank-weighted dollar-neutral) makes money in the 2025 bear/dispersion; a
breakout-long trend sleeve (Donchian 20/10) makes money in 2023/24 risk-on -- exactly
where PRL is weak. This builds both daily-marked on the full IS, reports each sleeve's
per-year/weekly/Sharpe, their correlation, and the combined curve (equal-weight and
vol-parity), to confirm the sum is more UNIFORM than either alone. Also a beta-hedged
PRL variant. IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.combined_book
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.anatomy import run_book
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.run_coarse import _warmup


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def _stats(d, name):
    d = d.dropna()
    wk = d.resample("W").sum(); mo = d.resample("ME").sum()
    eq = (1 + d).cumprod(); dd = (eq / eq.cummax() - 1).min()
    cagr = eq.iloc[-1] ** (252 / len(d)) - 1; sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    yr = {y: (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
    ys = " ".join(f"{y}:{v*100:+.0f}%" for y, v in yr.items())
    print(f"  {name:22s} CAGR={cagr*100:+.0f}% Sh={sh:+.2f} +wk={float((wk>0).mean())*100:.0f}% "
          f"+mo={float((mo>0).mean())*100:.0f}% maxDD={dd*100:.0f}%  [{ys}]")
    return d


def breakout_sleeve(close, ret, um, cost_mult=1.0):
    """Donchian 20/10 long trend book, equal-weight, daily-marked, breadth-scaled."""
    hi20 = _roll(close, 20, "max"); lo10 = _roll(close, 10, "min")
    entry = close >= hi20.shift(1)
    exit_ = close <= lo10.shift(1)
    sig = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    sig[entry] = 1.0; sig[exit_] = -1.0
    inlong = (sig.ffill() == 1.0) & um
    n = inlong.sum(axis=1)
    w = inlong.div(n.where(n > 0), axis=0).fillna(0.0)          # equal-weight the coins in a breakout
    breadth = (inlong.sum(axis=1) / um.sum(axis=1)).clip(0, 1)  # risk-on gauge
    gross = w.mul(breadth, axis=0)                              # scale exposure by breadth (risk-on)
    side = (PRIMARY.fee_bps + PRIMARY.half_spread_bps + PRIMARY.base_slippage_bps) * cost_mult / 1e4
    turn = gross.diff().abs().sum(axis=1)
    r = (gross.shift(1) * ret).sum(axis=1) - turn * side
    return r


def main():
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    close, um = m["close"], m["umask"]
    ret = close.pct_change(fill_method=None)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)

    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)
    j = pd.concat([prl.rename("PRL"), bo.rename("BO")], axis=1).dropna()
    prl, bo = j["PRL"], j["BO"]
    # beta-hedge PRL (net-short-beta -> even the years)
    mret = ret.where(um).mean(axis=1).reindex(prl.index).fillna(0.0)
    beta = (prl.rolling(60, min_periods=30).cov(mret) / mret.rolling(60, min_periods=30).var()).shift(1).fillna(0).clip(-3, 3)
    prl_bh = prl - beta * mret

    print("=== sleeves (daily-marked, IS) ===")
    _stats(prl, "PRL market-neutral")
    _stats(prl_bh, "PRL beta-hedged")
    _stats(bo, "Breakout-long trend")
    print(f"\n  corr(PRL, Breakout) = {prl.corr(bo):+.2f}   corr(PRL_bh, Breakout) = {prl_bh.corr(bo):+.2f}")

    print("\n=== combined (equal-weight and vol-parity) ===")
    for pl, plname in ((prl, "PRL"), (prl_bh, "PRLbh")):
        _stats(0.5 * pl + 0.5 * bo, f"{plname}+BO 50/50")
        sp, sb = pl.std(), bo.std()
        wp = (1 / sp) / (1 / sp + 1 / sb)
        _stats(wp * pl + (1 - wp) * bo, f"{plname}+BO vol-parity")


if __name__ == "__main__":
    main()
