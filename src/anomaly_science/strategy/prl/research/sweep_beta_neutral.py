"""Sweep-fade BETA-NEUTRAL: strip the BTC-drift beta, measure the honest regime-robust fade edge.

Credibility battery showed ~61% of the market-relative edge was the long-BTC hedge leg (BTC drift
in the 2023-24 rally), not the fade. The honest, regime-robust construction hedges each short with
a BETA-scaled BTC long (net beta = 0), so P&L = beta*BTC_ret - coin_ret = the coin's NEGATIVE
idiosyncratic return after the sweep (pure alpha, no market drift). Rolling causal beta. Report the
beta-neutral per-trade edge (per year), the sleeve, correlation with PRL, the three-sleeve book and
a $1000 sim -- the numbers we would actually trust before OOS. IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.sweep_beta_neutral --n 150
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, KFAIL, RETEST_WIN, MAXH = 20, 4, 12, 48
COST = 6 / 1e4


def build(n):
    panel = load_1h(_liquid(n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None)
    btc = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1))
    btc_ret = btc.pct_change(fill_method=None)
    beta = (ret.rolling(168, min_periods=48).cov(btc_ret).div(btc_ret.rolling(168, min_periods=48).var(), axis=0)).shift(1).clip(0, 3).fillna(1.0)
    btc = btc.to_numpy()
    hi_lvl = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= hi_lvl
    poke = (high >= hi_lvl) & (~above.shift(1).fillna(False)) & hi_lvl.notna()
    dh, dl, dc = high.resample("1D").max(), low.resample("1D").min(), close.resample("1D").last()
    dpc = dc.shift(1)
    dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
    datr_pct = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
    C, Hh, L, HL, BETA = (x.to_numpy() for x in (close, high, low, hi_lvl, beta))
    pk = poke.to_numpy(); rows = []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + KFAIL + RETEST_WIN + MAXH >= len(idx) or not (datr_pct[i, si] > 0):
                continue
            lv = HL[i, si]; j = None
            for t in range(i, i + KFAIL + 1):
                if C[t, si] < lv:
                    j = t; break
            if j is None:
                continue
            ext = np.nanmax(Hh[i:j + 1, si]); entry = None
            for u in range(j + 1, j + 1 + RETEST_WIN):
                if Hh[u, si] >= lv:
                    entry = lv; start = u + 1; break
            if entry is None:
                continue
            datrp = datr_pct[j, si]; stop = ext + 0.25 * datrp * entry; tp = entry * (1 - 1.0 * datrp)
            exitpx, xb = C[min(start + MAXH, len(idx) - 1), si], min(start + MAXH, len(idx) - 1)
            for u in range(start, min(start + MAXH, len(idx))):
                if C[u, si] >= stop:
                    exitpx, xb = C[u, si], u; break
                if L[u, si] <= tp:
                    exitpx, xb = tp, u; break
            coin_ret = exitpx / entry - 1.0; b = BETA[j, si]
            bn = b * (btc[xb] / btc[j] - 1.0) - coin_ret - COST     # beta-neutral short PnL
            full = (btc[xb] / btc[j] - 1.0) - coin_ret - COST       # full-hedge (headline)
            risk = (stop - entry) / entry
            rows.append(dict(entry_t=idx[start - 1], exit_t=idx[xb], bn=bn, full=full, risk=risk, date=idx[start - 1]))
    return pd.DataFrame(rows), idx


def sleeve(tr, hidx, col, risk_per=0.005, maxconc=20):
    hpos = {t: k for k, t in enumerate(hidx)}
    pnl = np.zeros(len(hidx)); open_exits = []
    for _, r in tr.sort_values("entry_t").iterrows():
        je, xe = hpos[r.entry_t], hpos[r.exit_t]
        open_exits = [e for e in open_exits if e > je]
        if len(open_exits) >= maxconc:
            continue
        open_exits.append(xe)
        pnl[xe] += (risk_per / max(r.risk, 1e-4)) * r[col]
    d = pd.Series(pnl, index=hidx).resample("1D").sum()
    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
    return d


def st(d):
    d = d.dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    return d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan, eq.iloc[-1] ** (252 / len(d)) - 1, dd


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    tr, hidx = build(args.n)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    print(f"trades: {len(tr)}")
    print("\n=== per-trade edge: full-hedge (headline) vs BETA-NEUTRAL (honest) ===")
    for y, g in tr.groupby("year"):
        print(f"    {y}: full {g.full.mean()*100:+.3f}%   beta-neutral {g.bn.mean()*100:+.3f}%")
    print(f"    ALL: full {tr.full.mean()*100:+.3f}%   beta-neutral {tr.bn.mean()*100:+.3f}%")

    # bootstrap CI on beta-neutral
    tr2 = tr.copy(); tr2["wk"] = pd.to_datetime(tr2["date"]).dt.tz_localize(None).dt.to_period("W")
    blocks = [g.bn.values for _, g in tr2.groupby("wk")]
    rng = np.random.default_rng(0); mn = [np.concatenate([blocks[k] for k in rng.integers(0, len(blocks), len(blocks))]).mean() for _ in range(2000)]
    lo, hi = np.percentile(mn, [2.5, 97.5])
    print(f"    beta-neutral 95% CI [{lo*100:+.3f}%, {hi*100:+.3f}%]  -> {'significant' if lo>0 else 'CROSSES 0'}")

    sw_bn = sleeve(tr, hidx, "bn")

    # PRL + breakout + combine
    from anomaly_science.strategy.xsect_momentum.research import panel as pn
    from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
    from anomaly_science.strategy.prl.research import factors as fx
    from anomaly_science.strategy.prl.research.anatomy import run_book
    from anomaly_science.strategy.prl.research.combined_book import breakout_sleeve
    from anomaly_science.strategy.prl.research.policy import PRIMARY
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p); close, um = m["close"], m["umask"]; ret = close.pct_change(fill_method=None)
    oo = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    score = oo.assign(date=pd.to_datetime(oo["date"], utc=True)).pivot(index="date", columns="symbol", values="score_linear").reindex_like(close)
    prl, _, _ = run_book(close, ret, um, score, p, step=15, hyst=0.5)
    bo = breakout_sleeve(close, ret, um)

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl, bo, sw_bn = norm(prl), norm(bo), norm(sw_bn)
    A = pd.concat([prl.rename("p"), bo.rename("b"), sw_bn.rename("s")], axis=1).dropna()
    print(f"\n=== beta-neutral sweep sleeve ===")
    s, c, dd = st(sw_bn); a = A["p"].corr(A["s"])
    print(f"    Sharpe {s:+.2f}  CAGR {c*100:+.0f}%  maxDD {dd*100:+.0f}%  corr(PRL) {a:+.2f}")

    def rp(cols_):
        al = A[cols_]; inv = 1 / al.std(); return (al * (inv / inv.sum())).sum(axis=1)
    print("\n=== three-sleeve book (honest, beta-neutral sweep) ===")
    for nm, d in (("PRL+BO", rp(["p", "b"])), ("PRL+BO+Sweep(bn)", rp(["p", "b", "s"]))):
        s, c, dd = st(d)
        ys = " ".join(f"{y}:{(1+g).prod()-1:+.0%}" for y, g in d.groupby(d.index.year))
        print(f"    {nm:20s} Sharpe {s:+.2f} CAGR {c*100:+.0f}% maxDD {dd*100:+.0f}% Calmar {c/abs(dd):.1f}  [{ys}]")
    book = rp(["p", "b", "s"]); L = 2.0
    eq = 1000 * (1 + L * book).cumprod(); eq = eq / eq.iloc[0] * 1000
    s, c, dd = st(L * book)
    print(f"\n=== $1000 @ 2x (honest beta-neutral three-sleeve) ===")
    print(f"    -> ${eq.iloc[-1]:,.0f}  CAGR {c*100:+.0f}%  maxDD {dd*100:+.0f}%  " + " ".join(f"{d.year}:${v:,.0f}" for d, v in eq.resample('YE').last().items()))


if __name__ == "__main__":
    main()
