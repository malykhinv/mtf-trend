"""Exhaustive 1h run (user): momentum (original), baskets, BTC, horizons, ATR, EMA,
funding -- everything we did on daily, on 1h full-period. One load, all sections.

Each section reports predictive value (rank-IC vs future residual, per year) and,
where relevant, a tradeable fade/continuation book. IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.hourly_full --n 150
"""

from __future__ import annotations

import argparse
import os
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import ic as icmod
from anomaly_science.strategy.prl.research import quality as ql
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h, _book
from anomaly_science.strategy.prl.research.peer import build_clusters, peer_loo
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.run_coarse import _warmup

FUND_DIR = ".output/market/binance_vision/um_futures/funding_v1"
YR = 24 * 365


def _icline(name, sc, label, p, ev, um):
    ic = icmod.daily_ic(sc.where(um), label, p, ev); s = icmod.summarize_ic(ic)
    ic.index = pd.to_datetime(ic.index)
    yr = {y: g.mean() for y, g in ic.groupby(ic.index.year)}
    ys = " ".join(f"{y}:{v:+.3f}" for y, v in yr.items())
    print(f"  {name:22s} IC={s['ic_mean']:+.4f} (t={s['t_stat']:+.1f})  [{ys}]")
    return ic


def _bookline(name, sc, close, ret, um, p, hold):
    for cm in (1.0, 2.0):
        d = _book(sc.where(um), close, ret, um, p, hold, cost_mult=cm)
        if not len(d):
            continue
        sh = d.mean() / d.std() * np.sqrt(YR); cagr = (1 + d).prod() ** (YR / len(d)) - 1
        wk = float((d.resample("W").sum() > 0).mean())
        print(f"    {name:20s} hold{hold} x{int(cm)}  CAGR={cagr*100:+.0f}% Sh={sh:+.2f} wk={wk*100:.0f}%")


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--hold", type=int, default=24)
    args = ap.parse_args()
    print(f"loading 1h panel (top-{args.n} liquid, full IS) ...")
    panel = load_1h(_liquid(args.n))
    p = replace(PRIMARY, mom_lbs=(24, 72, 168), skip=1, fwd_horizon=24, beta_lb=480,
                beta_min_periods=240, universe_n=100, liquidity_lb=720, min_age_days=720, min_xs=20)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, p.universe_n, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    close, um, eps, label, beta, rm = m["close"], m["umask"], m["eps"], m["label"], m["beta"], m["rm"]
    ret = close.pct_change(fill_method=None)
    high = pn.pivot(panel, "high").reindex_like(close); low = pn.pivot(panel, "low").reindex_like(close)
    open_ = pn.pivot(panel, "open").reindex_like(close)
    warm = _warmup(p); ev = close.index[warm:]
    sk = p.skip
    print(f"  panel {panel['symbol'].nunique()} syms x {len(close)} bars\n")

    def rank(x):
        return x.where(um).rank(axis=1, pct=True)

    # 1. MOMENTUM (original scenario) multi-horizon
    print("=== 1. MOMENTUM (IC vs future residual; neg=reversal) ===")
    for H in (6, 12, 24, 72, 168):
        momH = eps.rolling(H, min_periods=max(2, H // 2)).sum().shift(sk)
        fwd = ret.rolling(24, min_periods=24).sum().shift(-24)
        fwm = rm.rolling(24, min_periods=24).sum().shift(-24)
        lab24 = fwd.sub(beta.mul(fwm, axis=0))
        _icline(f"mom_{H}h", momH, lab24, p, ev, um)

    # 2. QUALITY
    print("\n=== 2. QUALITY / path ===")
    qf = ql.quality_features(eps, um, p, q_lb=168)
    for name in ("frac_pos", "burst", "path_eff"):
        _icline(name, qf[name], label, p, ev, um)
    comp = ql.quality_composite(qf, um)
    _icline("quality_composite", comp, label, p, ev, um)

    # 3. ATR / EMA / ATH context (IC vs future residual)
    print("\n=== 3. ATR / EMA / ATH-ATL context ===")
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    ctx = {"ecandle_range_atr": (high - low) / (atr + 1e-9),
           "dist_ath_all": (close / close.cummax() - 1).shift(sk),
           "dist_atl_all": (close / close.cummin() - 1).shift(sk)}
    for span in (24, 72, 168, 480):
        ctx[f"ema{span}_pos"] = (close / close.ewm(span=span, min_periods=span // 2).mean() - 1).shift(sk)
    for name, f in ctx.items():
        _icline(name, f, label, p, ev, um)

    # 4. BTC / market context conditioning of the book's weekly returns
    print("\n=== 4. BTC / market context (book daily return vs contemporaneous market) ===")
    mret = ret.where(um).mean(axis=1)
    btc = close["BTCUSDT"].pct_change() if "BTCUSDT" in close else pd.Series(0.0, index=close.index)
    d = _book(comp.where(um), close, ret, um, p, args.hold)
    j = pd.concat([d.rename("r"), mret.rename("mkt"), btc.rename("btc")], axis=1).dropna()
    print(f"  corr(book, market)={j.r.corr(j.mkt):+.3f}  corr(book, btc)={j.r.corr(j.btc):+.3f}  "
          f"(net-beta tilt if nonzero)")

    # 5. PEER BASKETS: momentum + convergence
    print("\n=== 5. CORRELATION BASKETS (peer-residual momentum & convergence) ===")
    assign, cdiag = build_clusters(eps, um, lookback=720, refit=168, k=12)
    peer = peer_loo(eps, assign); pr = (eps - peer).where(um)
    for lb in (12, 24, 72):
        sig = pr.rolling(lb, min_periods=max(2, lb // 2)).sum().shift(sk)
        fwd = pr.rolling(24, min_periods=12).sum().shift(-24)
        _icline(f"peer_resid_lb{lb} (neg=converge)", sig, fwd, p, ev, um)

    # 6. FUNDING (if downloaded)
    print("\n=== 6. FUNDING ===")
    fu = {}
    for s in close.columns:
        f = f"{FUND_DIR}/{s}.parquet"
        if os.path.exists(f):
            df = pd.read_parquet(f)
            t = pd.to_datetime(df["funding_time"], unit="ms", utc=True).dt.floor("h")
            fu[s] = df.groupby(t)["funding_rate"].sum()
    if fu:
        fund = pd.DataFrame(fu).reindex(close.index).reindex(columns=close.columns)
        cov = int(fund.notna().any().sum())
        print(f"  funding coverage: {cov} symbols")
        feats = {"fund_rate": fund.shift(sk),
                 "fund_cum_24h": _roll(fund, 24, "sum").shift(sk),
                 "fund_cum_7d": _roll(fund, 168, "sum").shift(sk)}
        for name, f in feats.items():
            _icline(name, f, label, p, ev, um)
    else:
        print("  (no funding files yet -- rerun after download completes)")


if __name__ == "__main__":
    main()
