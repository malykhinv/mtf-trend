"""What separates winning trades from losing trades — and can we cut the losers?
Plus: how does a recent pump/dump right before entry change the outcome? (user, deep dive)

Works on the existing top-100 book (OOF linear score). Each rebalance we take the
long candidates (top-decile score) and short candidates (bottom-decile), hold `step`
days, and label each name-trade a win/lose by its market-relative forward return
(long wins if it beats the cross-section; short wins if it lags). We then compare
entry features win-vs-lose, bucket by the recent pre-entry move, and test a loser cut.

IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.discriminate
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import feature_pool as fpm
from anomaly_science.strategy.prl.research.policy import PRIMARY as P
from anomaly_science.strategy.prl.research.run_coarse import _warmup

OUT = Path(".output/results/prl_coarse")
STEP = 15
Q = 0.10


def build_trades():
    oos = pd.Timestamp(P.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    qv = pn.pivot(panel, "quote_volume")
    umask = pn.build_universe_mask(panel, qv, P.universe_n, P.liquidity_lb, P.min_age_days)
    b = fpm.build_descriptors(panel, umask, P)
    d, umask, close = b["descriptors"], b["umask"], b["close"]
    ret = close.pct_change(fill_method=None)
    eps = fx.build_coarse(panel, umask, P)["eps"]

    # recent pre-entry moves (raw + residual), causal (shifted skip)
    d = dict(d)
    d["r1d"] = ret.shift(P.skip)
    d["r3d"] = (close.shift(P.skip) / close.shift(P.skip + 3) - 1.0)
    d["r5d"] = (close.shift(P.skip) / close.shift(P.skip + 5) - 1.0)
    d["resid3d"] = eps.rolling(3, min_periods=2).sum().shift(P.skip)

    oof = pd.read_parquet(OUT / "oof_scores_prl.parquet")
    oof["date"] = pd.to_datetime(oof["date"], utc=True)
    score = oof.pivot(index="date", columns="symbol", values="score_linear").reindex(
        index=close.index, columns=close.columns)

    idx = close.index
    feat_names = list(d.keys())
    rows = []
    for ri in range(_warmup(P), len(idx) - STEP - 2):
        if (ri - _warmup(P)) % STEP:  # non-overlapping
            continue
        ei, xi = ri + 1, ri + 1 + STEP
        if xi >= len(idx):
            break
        s = score.iloc[ri].where(umask.iloc[ri]).dropna()
        if len(s) < 40:
            continue
        fwd = (close.iloc[xi] / close.iloc[ei] - 1.0).reindex(s.index)
        mkt = fwd.mean()
        rel = fwd - mkt
        order = s.sort_values(ascending=False)
        k = max(1, int(len(order) * Q))
        for side, syms in ((+1, order.index[:k]), (-1, order.index[-k:])):
            for sym in syms:
                r = rel.get(sym, np.nan)
                if not np.isfinite(r):
                    continue
                row = {"date": idx[ri], "symbol": sym, "side": side, "rel": float(r),
                       "win": int((r > 0) if side > 0 else (r < 0))}
                for fn in feat_names:
                    row[fn] = d[fn].iloc[ri].get(sym, np.nan)
                rows.append(row)
    return pd.DataFrame(rows), feat_names


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tr, feats = build_trades()
    print(f"trades: {len(tr)}  longs={int((tr.side>0).sum())}  shorts={int((tr.side<0).sum())}")
    for side, name in ((+1, "LONG"), (-1, "SHORT")):
        g = tr[tr.side == side]
        print(f"\n=== {name}: base win-rate={g.win.mean():.3f}  mean rel={g.rel.mean()*100:+.2f}% ===")
        # which features separate win vs lose (standardized mean gap)
        diffs = []
        for f in feats:
            a, b = g.loc[g.win == 1, f], g.loc[g.win == 0, f]
            sd = g[f].std()
            if sd > 0 and a.notna().sum() > 30 and b.notna().sum() > 30:
                diffs.append((f, (a.mean() - b.mean()) / sd))
        diffs.sort(key=lambda x: -abs(x[1]))
        print("  top separators (win - lose, in std units):")
        for f, dv in diffs[:8]:
            print(f"    {f:18s} {dv:+.3f}")

    # pump/dump effect: win-rate & mean rel by recent-3d-move quintile
    print("\n=== recent pre-entry 3d move (pump/dump) vs outcome ===")
    tr["r3d_q"] = pd.qcut(tr["r3d"], 5, labels=["dump--", "dump-", "flat", "pump+", "pump++"], duplicates="drop")
    for side, name in ((+1, "LONG"), (-1, "SHORT")):
        g = tr[tr.side == side]
        w = g.groupby("r3d_q", observed=True)["win"].mean()
        m = g.groupby("r3d_q", observed=True)["rel"].mean() * 100
        print(f"  {name:5s} win-rate: " + "  ".join(f"{q}={w.get(q,np.nan):.2f}" for q in w.index))
        print(f"  {name:5s} mean rel: " + "  ".join(f"{q}={m.get(q,np.nan):+.2f}%" for q in m.index))

    tr.to_parquet(OUT / "discriminate_trades.parquet")
    print(f"\nwrote {OUT}/discriminate_trades.parquet")


if __name__ == "__main__":
    main()
