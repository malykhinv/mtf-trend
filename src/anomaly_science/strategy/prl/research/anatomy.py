"""Anatomy of the daily top-100 book: is ~20%/yr a fluctuation, how many trades,
where does the P&L concentrate (weeks/months), and does trimming the middle/losers
raise BOTH return and positive-weeks? (user deep-dive)

IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.anatomy
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.run_coarse import _warmup

OUT = Path(".output/results/prl_coarse")


def _load(universe_n=100):
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=universe_n)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, universe_n, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    close, um = m["close"], m["umask"]
    ret = close.pct_change(fill_method=None)
    oof = pd.read_parquet(OUT / "oof_scores_prl.parquet"); oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex(index=close.index, columns=close.columns)
    return p, close, ret, um, score


def run_book(close, ret, um, score, p, step=15, hyst=0.5, cost_mult=1.0, keep_tail=1.0):
    """keep_tail<1 zeroes the middle: keep only top/bottom `keep_tail` fraction by score."""
    idx = close.index; daily = pd.Series(0.0, index=idx); prev = pd.Series(dtype=float)
    side = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * cost_mult / 1e4
    n_names, n_reb = 0, 0
    for ri in range(_warmup(p), len(idx) - step - 1, step):
        s = score.iloc[ri].where(um.iloc[ri]).dropna()
        if len(s) < max(p.min_xs, 15):
            continue
        rk = s.rank(pct=True); w = rk - rk.mean()
        if keep_tail < 1.0:
            lo, hi = keep_tail, 1 - keep_tail
            w[(rk > lo) & (rk < hi)] = 0.0
        if w.abs().sum() == 0:
            continue
        w = w / w.abs().sum()
        if hyst > 0 and len(prev):
            w = (1 - hyst) * w + hyst * prev.reindex(w.index).fillna(0.0); w = w - w.mean(); w = w / w.abs().sum()
        turn = float((w.subtract(prev, fill_value=0.0)).abs().sum())
        n_names += int((w.abs() > 1e-9).sum()); n_reb += 1
        for dd in range(ri + 1, min(ri + 1 + step, len(idx))):
            daily.iloc[dd] += float((w * ret.iloc[dd].reindex(w.index)).sum(skipna=True))
        daily.iloc[ri + 1] -= turn * side
        prev = w
    d = daily.loc[daily.ne(0).cumsum() > 0]
    return d, n_names, n_reb


def _stats(d, step):
    wk = d.resample("W").sum(); mo = d.resample("ME").sum()
    eq = (1 + d).cumprod(); cagr = eq.iloc[-1] ** (252 / len(d)) - 1
    sh = d.mean() / d.std() * np.sqrt(252)
    tstat = d.mean() / (d.std() / np.sqrt(len(d)))
    return dict(cagr=cagr, sharpe=sh, tstat=tstat, wk=wk, mo=mo,
                wk_pos=float((wk > 0).mean()), mo_pos=float((mo > 0).mean()))


def main() -> None:
    p, close, ret, um, score = _load(100)
    d, n_names, n_reb = run_book(close, ret, um, score, p)
    st = _stats(d, 15)
    ndays = len(d)
    print("=== ANATOMY: top-100 daily book (step=15, hyst=0.5, cost x1) ===")
    print(f"  span {d.index.min().date()}..{d.index.max().date()}  trading days={ndays}")
    print(f"  rebalances={n_reb}  avg names/rebalance={n_names/max(n_reb,1):.0f}  "
          f"total name-trades={n_names}  ~trades/day={n_names/ndays:.1f}")
    print(f"  CAGR={st['cagr']*100:+.1f}%  Sharpe={st['sharpe']:.2f}  t-stat={st['tstat']:.2f}  "
          f"(t>2 => not pure noise)")
    print(f"  +weeks={st['wk_pos']*100:.0f}% (n={len(st['wk'])})  +months={st['mo_pos']*100:.0f}% (n={len(st['mo'])})")

    # concentration: share of TOTAL return from the best few weeks/months
    wk = st["wk"]; mo = st["mo"]; tot = wk.sum()
    top5w = wk.sort_values(ascending=False).head(5).sum() / tot
    top3m = mo.sort_values(ascending=False).head(3).sum() / mo.sum()
    # remove-top-weeks robustness
    def remove_top(s, k):
        return s.sort_values(ascending=False).iloc[k:].sum()
    flipw = next((k for k in range(len(wk)) if remove_top(wk, k) <= 0), len(wk))
    print(f"\n  concentration: best-5-weeks = {top5w*100:.0f}% of total return  "
          f"best-3-months = {top3m*100:.0f}% of total")
    print(f"  remove best {flipw} of {len(wk)} weeks ({flipw/len(wk)*100:.0f}%) -> total <= 0")
    print(f"  weekly:  mean={wk.mean()*100:+.2f}%  best={wk.max()*100:+.1f}%  worst={wk.min()*100:+.1f}%  "
          f"std={wk.std()*100:.2f}%")
    print(f"  monthly: mean={mo.mean()*100:+.2f}%  best={mo.max()*100:+.1f}%  worst={mo.min()*100:+.1f}%")
    yrs = {y: (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
    print("  by year: " + "  ".join(f"{y}:{v*100:+.0f}%" for y, v in yrs.items()))

    # selectivity: trim the middle, keep only conviction tails
    print("\n=== selectivity sweep: keep only top/bottom fraction (trim the middle) ===")
    print(f"  {'keep_tail':>9} {'CAGR':>7} {'Sharpe':>7} {'+weeks':>7} {'+months':>8} {'names/reb':>9}")
    for kt in (0.5, 0.3, 0.2, 0.1, 0.05):
        dd, nn, nr = run_book(close, ret, um, score, p, keep_tail=kt)
        s2 = _stats(dd, 15)
        print(f"  {kt:9.2f} {s2['cagr']*100:+6.0f}% {s2['sharpe']:7.2f} {s2['wk_pos']*100:6.0f}% "
              f"{s2['mo_pos']*100:7.0f}% {nn/max(nr,1):9.0f}")


if __name__ == "__main__":
    main()
