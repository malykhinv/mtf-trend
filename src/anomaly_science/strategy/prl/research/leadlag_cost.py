"""Lead-lag: is there a GROSS edge, and at what cost does it survive? (turnover was the killer)

The hourly lead-lag sleeve lost -100% at 6bps -- but so did its SHUFFLE, i.e. TURNOVER cost
dominated and the signal question was never answered. Here we (1) measure GROSS (0bps) first
-- does any edge exist? -- then (2) sweep realistic costs (maker ~1-2bps for a limit-posting
market-neutral book), (3) reduce turnover via SPARSE trading (only the extreme signal decile)
and longer holds, to find the cost/frequency point where an uncorrelated intraday edge becomes
net-positive. If gross <= 0 the mechanic is simply dead. IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.leadlag_cost --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h


def book(score, ret, h, side_bps, sparse):
    """Rank-weighted dollar-neutral, h-hour overlapping tranches. sparse=fraction each side (e.g. 0.1)."""
    side = side_bps / 1e4
    books = []
    for ph in range(h):
        w_prev = None; daily = pd.Series(0.0, index=ret.index)
        for ri in range(ph, len(ret) - 1, h):
            s = score.iloc[ri].dropna()
            if len(s) < 20:
                continue
            rk = s.rank(pct=True)
            if sparse < 0.5:
                w = pd.Series(0.0, index=s.index)
                w[rk >= 1 - sparse] = 1.0; w[rk <= sparse] = -1.0
                if w.abs().sum() == 0:
                    continue
                w = w / w.abs().sum()
            else:
                w = rk - rk.mean(); w = w / w.abs().sum()
            turn = float((w.subtract(w_prev, fill_value=0.0)).abs().sum()) if w_prev is not None else 1.0
            for dd in range(ri + 1, min(ri + 1 + h, len(ret))):
                daily.iloc[dd] += float((w * ret.iloc[dd].reindex(w.index)).sum(skipna=True)) / h
            daily.iloc[ri + 1] -= turn * side / h
            w_prev = w
        books.append(daily)
    return pd.concat(books, axis=1).sum(axis=1)


def sh(d):
    d = d.dropna(); return d.mean() / d.std() * np.sqrt(252 * 24) if d.std() > 0 else np.nan


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    close = panel.pivot(index="date", columns="symbol", values="close")
    ret = close.pct_change(fill_method=None)
    leader = ret.mean(axis=1)
    w = 168
    beta = (ret.rolling(w, min_periods=48).cov(leader).div(leader.rolling(w, min_periods=48).var(), axis=0)).shift(1)

    print("\n=== GROSS-first, then cost sweep (Sharpe, annualized) ===")
    print(f"  {'k':>2} {'h':>3} {'mode':>6}  {'gross':>7} {'1bps':>7} {'2bps':>7} {'4bps':>7}")
    for k in (1, 2, 3):
        exp = beta.mul(leader.rolling(k, min_periods=1).sum(), axis=0)
        own = ret.rolling(k, min_periods=1).sum()
        signal = (exp - own).shift(1)
        for h in (3, 6, 12):
            for mode, sp in (("full", 1.0), ("decile", 0.1)):
                g = sh(book(signal, ret, h, 0.0, sp))
                c1 = sh(book(signal, ret, h, 1.0, sp))
                c2 = sh(book(signal, ret, h, 2.0, sp))
                c4 = sh(book(signal, ret, h, 4.0, sp))
                print(f"  {k:>2} {h:>3} {mode:>6}  {g:>+7.2f} {c1:>+7.2f} {c2:>+7.2f} {c4:>+7.2f}")


if __name__ == "__main__":
    main()
