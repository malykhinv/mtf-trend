"""Overlapping-tranche (continuous rebalance) + holding-period grid.

The single-phase book rebalances the WHOLE portfolio every H days -> lumpy weekly
P&L (few independent bets/week). Overlapping tranches instead rebalance 1/H of the
book every day (each tranche held H days, entered on consecutive days) = the average
of H phase-shifted books. Same signal, same edge, far smoother equity -> the direct
lever on the positive-weeks ceiling the user is pushing on.

We sweep the holding period H and compare single-phase vs overlapping. IS only.
Run: python -m anomaly_science.strategy.prl.research.overlap
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.anatomy import _load, _stats
from anomaly_science.strategy.prl.research.run_coarse import _warmup


def _phase_book(close, ret, um, score, p, H, phase, hyst, cost_mult):
    """One tranche: rebalance on days ri ≡ (warmup+phase) mod H, hold H days."""
    idx = close.index; daily = pd.Series(0.0, index=idx); prev = pd.Series(dtype=float)
    side = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * cost_mult / 1e4
    for ri in range(_warmup(p) + phase, len(idx) - H - 1, H):
        s = score.iloc[ri].where(um.iloc[ri]).dropna()
        if len(s) < max(p.min_xs, 15):
            continue
        rk = s.rank(pct=True); w = rk - rk.mean(); w = w / w.abs().sum()
        if hyst > 0 and len(prev):
            w = (1 - hyst) * w + hyst * prev.reindex(w.index).fillna(0.0); w = w - w.mean(); w = w / w.abs().sum()
        turn = float((w.subtract(prev, fill_value=0.0)).abs().sum())
        for dd in range(ri + 1, min(ri + 1 + H, len(idx))):
            daily.iloc[dd] += float((w * ret.iloc[dd].reindex(w.index)).sum(skipna=True))
        daily.iloc[ri + 1] -= turn * side
        prev = w
    return daily


def overlapping(close, ret, um, score, p, H, hyst=0.5, cost_mult=1.0):
    """Average of H daily-offset tranches = continuous rebalance."""
    books = [_phase_book(close, ret, um, score, p, H, ph, hyst, cost_mult) for ph in range(H)]
    daily = pd.concat(books, axis=1).mean(axis=1)
    return daily.loc[daily.ne(0).cumsum() > 0]


def main() -> None:
    p, close, ret, um, score = _load(100)
    print("=== holding-period grid: SINGLE-phase vs OVERLAPPING tranches (top-100, cost x1) ===")
    print(f"  {'H':>3}  {'--- single-phase ---':>28}   {'--- overlapping ---':>28}")
    print(f"  {'':>3}  {'CAGR':>6} {'Sharpe':>6} {'+wk':>5} {'+mo':>5}   {'CAGR':>6} {'Sharpe':>6} {'+wk':>5} {'+mo':>5}")
    for H in (5, 7, 10, 15, 20, 25, 30):
        ds = _phase_book(close, ret, um, score, p, H, 0, 0.5, 1.0)
        ds = ds.loc[ds.ne(0).cumsum() > 0]; ss = _stats(ds, H)
        do = overlapping(close, ret, um, score, p, H); so = _stats(do, H)
        print(f"  {H:>3}  {ss['cagr']*100:+5.0f}% {ss['sharpe']:6.2f} {ss['wk_pos']*100:4.0f}% {ss['mo_pos']*100:4.0f}%"
              f"   {so['cagr']*100:+5.0f}% {so['sharpe']:6.2f} {so['wk_pos']*100:4.0f}% {so['mo_pos']*100:4.0f}%")


if __name__ == "__main__":
    main()
