"""How rolling market / BTC context and EMA position condition each leg SEPARATELY.

For every rebalance we take the LONG-leg and SHORT-leg raw return over the hold, and
the causal ENTRY context: trailing market & BTC rolling returns (1/2/3/7/14d) and
market & BTC position vs EMA 20/50/100/200. We report, per leg, the correlation of
each context with the leg's next-period return and its per-year sign stability -- i.e.
which market/BTC trend favors longs vs shorts (a leg-conditioning signal).

IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.leg_market_context
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.run_coarse import _warmup


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

    # market & BTC context (all causal, value at ri known at close ri)
    mret_d = ret.where(um).mean(axis=1).fillna(0.0)
    mkt_idx = (1 + mret_d).cumprod()
    btc = close["BTCUSDT"]; btc_ret_d = btc.pct_change().fillna(0.0)
    ctx = {}
    for N in (1, 2, 3, 7, 14):
        ctx[f"mkt_ret_{N}d"] = mret_d.rolling(N).sum()
        ctx[f"btc_ret_{N}d"] = btc_ret_d.rolling(N).sum()
    for span in (20, 50, 100, 200):
        ctx[f"mkt_ema{span}"] = mkt_idx / mkt_idx.ewm(span=span, min_periods=span // 2).mean() - 1
        ctx[f"btc_ema{span}"] = btc / btc.ewm(span=span, min_periods=span // 2).mean() - 1
    ctx = pd.DataFrame(ctx)

    idx = close.index
    rows = []
    prev = pd.Series(dtype=float)
    for ri in range(_warmup(p), len(idx) - 16, 15):
        s = score.iloc[ri].where(um.iloc[ri]).dropna()
        if len(s) < 15:
            continue
        rk = s.rank(pct=True); w = rk - rk.mean(); w = w / w.abs().sum()
        if len(prev):
            w = 0.5 * w + 0.5 * prev.reindex(w.index).fillna(0); w = w - w.mean(); w = w / w.abs().sum()
        wl, ws = w[w > 0], w[w < 0]
        lr = sr = 0.0
        for dd in range(ri + 1, min(ri + 16, len(idx))):
            r = ret.iloc[dd]
            lr += float((wl * r.reindex(wl.index)).sum(skipna=True))
            sr += float((ws * r.reindex(ws.index)).sum(skipna=True))
        row = {"date": idx[ri], "long_ret": lr, "short_ret": sr}
        for c in ctx.columns:
            row[c] = ctx[c].iloc[ri]
        rows.append(row)
        prev = w
    tr = pd.DataFrame(rows)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    cfeats = list(ctx.columns)
    print(f"rebalances: {len(tr)}\n")

    for leg in ("long_ret", "short_ret"):
        print(f"=== {leg.upper()} vs entry context (corr; * sign-stable across years) ===")
        rr = []
        for c in cfeats:
            j = tr[[c, leg, "year"]].dropna()
            if len(j) < 20:
                continue
            corr = j[c].corr(j[leg])
            signs = [np.sign(gy[c].corr(gy[leg])) for y, gy in j.groupby("year") if len(gy) > 5]
            stable = len(signs) > 1 and all(sg == signs[0] for sg in signs if sg == sg)
            rr.append((c, corr, stable))
        rr.sort(key=lambda x: -abs(x[1]))
        for c, corr, stable in rr:
            print(f"    {c:14s} corr={corr:+.3f}{' *' if stable else ''}")
        print()


if __name__ == "__main__":
    main()
