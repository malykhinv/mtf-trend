"""Cross-sectional intraday LEAD-LAG market-neutral sleeve -> a NEW uncorrelated stream?

Market-logic: a leader (BTC / the EW market) moves first; smaller/slower coins lag and catch
up (Epps effect). If a coin's expected move (beta x leader's recent return) exceeds its realized
move, it is a LAGGARD due to catch up -> go long the laggards, short the over-reactors, dollar-
neutral. This is an INTRADAY, non-momentum mechanic distinct from daily PRL -> if it has its own
Calmar and is uncorrelated with PRL, it lifts the combined Calmar (the binding constraint).

Sweep lookback k and hold h; rank-weighted dollar-neutral, hourly overlapping tranches, cost per
turnover. Report Sharpe/Calmar/+wk, per-year, correlation with the daily PRL sleeve, and a shuffle
control (permute the signal cross-sectionally). IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.leadlag --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

SIDE = 6.0 / 1e4    # ~6bps per unit turnover (fee+half-spread+slip)


def stats(d, name, ref=None):
    d = d.dropna()
    if len(d) < 50:
        print(f"  {name:22s} (short)"); return d
    wk = d.resample("W").sum(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    cagr = eq.iloc[-1] ** (252 * 24 / len(d)) - 1 if len(d) > 0 else np.nan   # hourly -> annualize
    sh = d.mean() / d.std() * np.sqrt(252 * 24) if d.std() > 0 else np.nan
    yr = {int(y): (1 + g).prod() - 1 for y, g in d.groupby(d.index.year)}
    ys = " ".join(f"{y}:{v*100:+.0f}%" for y, v in yr.items())
    cs = ""
    if ref is not None:
        dd_ = d.resample("1D").apply(lambda x: (1 + x).prod() - 1)
        a, b = dd_.align(ref, join="inner"); cs = f"  corr(PRLd)={a.corr(b):+.2f}"
    print(f"  {name:22s} Sh={sh:+.2f} Cal={cagr/abs(dd) if dd<0 else float('nan'):+.1f} +wk={float((wk>0).mean())*100:.0f}% maxDD={dd*100:+.0f}%{cs}  [{ys}]")
    return d


def book(score, ret, h):
    """Rank-weighted dollar-neutral, h-hour overlapping tranches, cost per turnover."""
    books = []
    for ph in range(h):
        w_prev = None; daily = pd.Series(0.0, index=ret.index)
        idxs = range(ph, len(ret) - 1, h)
        for ri in idxs:
            s = score.iloc[ri].dropna()
            if len(s) < 15:
                continue
            rk = s.rank(pct=True); w = rk - rk.mean(); w = w / w.abs().sum()
            turn = float((w.subtract(w_prev, fill_value=0.0)).abs().sum()) if w_prev is not None else 1.0
            for dd in range(ri + 1, min(ri + 1 + h, len(ret))):
                daily.iloc[dd] += float((w * ret.iloc[dd].reindex(w.index)).sum(skipna=True)) / h
            daily.iloc[ri + 1] -= turn * SIDE / h
            w_prev = w
        books.append(daily)
    return pd.concat(books, axis=1).sum(axis=1)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    close = panel.pivot(index="date", columns="symbol", values="close")
    ret = close.pct_change(fill_method=None)
    leader = ret.mean(axis=1)                       # EW market leader
    # causal beta to leader
    w = 168
    cov = ret.rolling(w, min_periods=48).cov(leader); var = leader.rolling(w, min_periods=48).var()
    beta = cov.div(var, axis=0).shift(1)

    # daily PRL for correlation
    try:
        oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
        # reuse the daily combined? simplest: use PRL daily returns proxy via score not available here -> skip if absent
    except Exception:
        pass
    prl_daily = None
    try:
        from dataclasses import replace
        from anomaly_science.strategy.xsect_momentum.research import panel as pn
        from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
        from anomaly_science.strategy.prl.research import factors as fx
        from anomaly_science.strategy.prl.research.anatomy import run_book
        from anomaly_science.strategy.prl.research.policy import PRIMARY
        oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
        dp = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
        p = replace(PRIMARY, universe_n=100); qv = pn.pivot(dp, "quote_volume")
        um = pn.build_universe_mask(dp, qv, 100, p.liquidity_lb, p.min_age_days)
        m = fx.build_coarse(dp, um, p); dc = m["close"]; dr = dc.pct_change(fill_method=None)
        sc = oof.assign(date=pd.to_datetime(oof["date"], utc=True)).pivot(index="date", columns="symbol", values="score_linear").reindex_like(dc)
        prl_daily, _, _ = run_book(dc, dr, um, sc, p, step=15, hyst=0.5)
        prl_daily.index = pd.to_datetime(prl_daily.index).tz_localize(None).normalize()
    except Exception as e:
        print("PRL daily unavailable:", e)

    print("\n=== lead-lag laggard-catchup sleeve (k lookback, h hold) ===")
    rng = np.random.default_rng(0)
    for k in (1, 2, 3, 6):
        exp = beta.mul(leader.rolling(k, min_periods=1).sum(), axis=0)     # expected move from leader
        own = ret.rolling(k, min_periods=1).sum()
        signal = (exp - own).shift(1)                                       # laggards (positive) should catch up
        for h in (1, 3, 6):
            d = book(signal, ret, h)
            d.index = pd.to_datetime(d.index)
            dl = d.copy(); dl.index = dl.index.tz_localize(None) if dl.index.tz else dl.index
            stats(dl, f"k={k} h={h}", ref=prl_daily)
        # shuffle control at h=3
        sig_sh = pd.DataFrame(rng.permuted(signal.to_numpy(), axis=1), index=signal.index, columns=signal.columns)
        dsh = book(sig_sh, ret, 3); dsh.index = pd.to_datetime(dsh.index)
        dsh.index = dsh.index.tz_localize(None) if dsh.index.tz else dsh.index
        stats(dsh, f"k={k} h=3 SHUFFLED")
        print()


if __name__ == "__main__":
    main()
