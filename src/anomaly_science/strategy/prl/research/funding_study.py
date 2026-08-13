"""Funding-rate influence on the daily book (§23.1): feature, confound, conditioner.

Now that funding is downloaded full-period, test: (1) does funding predict future
residual return (feature)? (2) is the signal's edge partly funding carry (confound)?
(3) does extreme funding condition reversals (crowded longs pay -> squeeze)?

Funding is aggregated to daily (sum of intraperiod rates = daily carry). Causal:
funding known at its funding_time; we use trailing/current-day values for decision t,
target measured forward. IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.funding_study
"""

from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import ic as icmod
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.run_coarse import _warmup
from dataclasses import replace

FUND_DIR = ".output/market/binance_vision/um_futures/funding_v1"


def load_funding_daily(symbols, index):
    """Daily funding matrix (date x symbol): sum of intraday funding rates per day."""
    cols = {}
    for s in symbols:
        f = f"{FUND_DIR}/{s}.parquet"
        if not os.path.exists(f):
            continue
        df = pd.read_parquet(f)
        t = pd.to_datetime(df["funding_time"], unit="ms", utc=True).dt.floor("D")
        daily = df.groupby(t)["funding_rate"].sum()
        cols[s] = daily
    if not cols:
        return None
    fund = pd.DataFrame(cols).reindex(index)
    return fund


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def main():
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    close, um, eps, label, mom = m["close"], m["umask"], m["eps"], m["score"], m["score"]

    syms = list(close.columns)
    print(f"loading funding for {len(syms)} symbols ...")
    fund = load_funding_daily(syms, close.index)
    have = fund.notna().any()
    print(f"  funding coverage: {int(have.sum())} symbols  "
          f"dates {fund.dropna(how='all').index.min().date()}..{fund.dropna(how='all').index.max().date()}")
    fund = fund.reindex(columns=close.columns)

    sk = p.skip
    feats = {
        "fund_rate": fund.shift(sk),
        "fund_cum_7d": _roll(fund, 7, "sum").shift(sk),
        "fund_cum_30d": _roll(fund, 30, "sum").shift(sk),
        "fund_z30": ((fund - _roll(fund, 30, "mean")) / (_roll(fund, 30, "std") + 1e-9)).shift(sk),
    }
    warm = _warmup(p)
    ev = close.index[warm:]

    print("\n=== funding as FEATURE: rank-IC vs future residual (per year) ===")
    for name, f in feats.items():
        ic = icmod.daily_ic(f.where(um), label, p, ev)
        s = icmod.summarize_ic(ic)
        ic.index = pd.to_datetime(ic.index)
        yr = {y: g.mean() for y, g in ic.groupby(ic.index.year)}
        ys = " ".join(f"{y}:{v:+.3f}" for y, v in yr.items())
        print(f"  {name:12s} IC={s['ic_mean']:+.4f} (t={s['t_stat']:+.1f})  [{ys}]")

    # confound: is the working signal (momentum rank) correlated with funding rank?
    print("\n=== funding as CONFOUND: per-date corr(signal rank, funding rank) ===")
    fr = feats["fund_cum_7d"].where(um).rank(axis=1, pct=True)
    sr = mom.where(um).rank(axis=1, pct=True)
    corrs = []
    for d in ev:
        a, b = sr.loc[d], fr.loc[d]
        ok = a.notna() & b.notna()
        if ok.sum() > 20:
            corrs.append(np.corrcoef(a[ok], b[ok])[0, 1])
    print(f"  mean corr(momentum, 7d funding) = {np.nanmean(corrs):+.3f}  "
          f"(high => the 'edge' may be funding carry)")

    # conditioner: does extreme funding precede reversal? high funding -> future residual sign
    print("\n=== funding as CONDITIONER: future residual by funding bucket ===")
    frq = feats["fund_cum_7d"].where(um).rank(axis=1, pct=True)
    lab = label.where(um)
    rows = {"low_fund": [], "mid": [], "high_fund": []}
    for d in ev:
        q = frq.loc[d]; l = lab.loc[d]
        ok = q.notna() & l.notna()
        if ok.sum() < 30:
            continue
        rows["low_fund"].append(l[ok][q[ok] < 0.2].mean())
        rows["mid"].append(l[ok][(q[ok] >= 0.2) & (q[ok] <= 0.8)].mean())
        rows["high_fund"].append(l[ok][q[ok] > 0.8].mean())
    for k, v in rows.items():
        print(f"  {k:10s} mean future residual = {np.nanmean(v)*100:+.3f}%")
    print("  (high-funding future residual << mid => crowded-long reversal)")


if __name__ == "__main__":
    main()
