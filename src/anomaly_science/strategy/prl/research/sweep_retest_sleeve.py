"""Sweep-fade RETEST sleeve: turn the +0.56%/trade retest edge into a risk-managed book.

The limit-retest short (fill on the bounce to the sweep-high, stop just above, target ~1x 1d-ATR)
is +0.56%/trade market-relative and year-stable. Here we build it as a proper market-neutral
sleeve: each fill sized to a FIXED fractional risk (stop distance known), concurrent-position cap
(sweeps cluster), cost 6bps, daily mark-to-market on the MARKET-RELATIVE return (short coin + market
hedge). Report Sharpe/Calmar/per-year/maxDD, correlation with the daily PRL book, and a vol-parity
combine (does an uncorrelated short-reversion sleeve lift the ~3.3 Calmar ceiling?). IS only, OOS
reserved. Run: python -m anomaly_science.strategy.prl.research.sweep_retest_sleeve --n 150
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, KFAIL, RETEST_WIN, MAXH = 20, 4, 12, 48
FBUF, KTP, F = 0.25, 1.0, 1.0        # stop buf (1dATR), target (1dATR), retest at sweep-high
COST = 6 / 1e4


def build(n):
    panel = load_1h(_liquid(n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low = (px(c) for c in ("close", "high", "low"))
    idx, cols = close.index, close.columns
    btc = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)).to_numpy()
    hi_lvl = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= hi_lvl
    poke = (high >= hi_lvl) & (~above.shift(1).fillna(False)) & hi_lvl.notna()
    dh, dl, dc = high.resample("1D").max(), low.resample("1D").min(), close.resample("1D").last()
    dpc = dc.shift(1)
    dtr = (dh - dl).combine((dh - dpc).abs(), np.maximum).combine((dl - dpc).abs(), np.maximum)
    datr_pct = ((dtr.rolling(14, min_periods=7).mean().shift(1)) / dc.shift(1)).reindex(idx, method="ffill").to_numpy()
    C, Hh, L, HL = (x.to_numpy() for x in (close, high, low, hi_lvl))
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
            ext = np.nanmax(Hh[i:j + 1, si]); datrp = datr_pct[i, si]
            limitp = lv + F * (ext - lv); entry = None
            for t in range(j + 1, j + 1 + RETEST_WIN):
                if Hh[t, si] >= limitp:
                    entry = limitp; start = t + 1; break
            if entry is None:
                continue
            stop = ext + FBUF * datrp * entry; tp = entry * (1 - KTP * datrp)
            out, exitpx, xb = "time", C[min(start + MAXH, len(idx) - 1), si], min(start + MAXH, len(idx) - 1)
            for t in range(start, min(start + MAXH, len(idx))):
                if Hh[t, si] >= stop:
                    out, exitpx, xb = "stop", stop, t; break
                if L[t, si] <= tp:
                    out, exitpx, xb = "tp", tp, t; break
            mrel = (entry - exitpx) / entry + (btc[xb] / btc[j] - 1.0)
            risk = (stop - entry) / entry
            rows.append(dict(entry_t=idx[start - 1], exit_t=idx[xb], mrel=mrel, risk=risk))
    return pd.DataFrame(rows), idx


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    tr, idx = build(args.n)
    print(f"retest fills: {len(tr)}  mean market-rel {tr.mrel.mean()*100:+.3f}%")
    hpos = {t: k for k, t in enumerate(idx)}

    def sleeve(risk_per, maxconc):
        pnl_h = np.zeros(len(idx)); open_exits = []
        for _, r in tr.sort_values("entry_t").iterrows():
            je, xe = hpos[r.entry_t], hpos[r.exit_t]
            open_exits = [e for e in open_exits if e > je]
            if len(open_exits) >= maxconc:
                continue
            open_exits.append(xe)
            w = risk_per / max(r.risk, 1e-4)
            pnl_h[xe] += w * (r.mrel - COST)
        d = pd.Series(pnl_h, index=idx).resample("1D").sum()
        d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
        return d

    prl = None
    try:
        from anomaly_science.strategy.xsect_momentum.research import panel as pn
        from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
        from anomaly_science.strategy.prl.research import factors as fx
        from anomaly_science.strategy.prl.research.anatomy import run_book
        from anomaly_science.strategy.prl.research.policy import PRIMARY
        oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
        dp = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
        p = replace(PRIMARY, universe_n=100); dqv = pn.pivot(dp, "quote_volume")
        um = pn.build_universe_mask(dp, dqv, 100, p.liquidity_lb, p.min_age_days)
        m = fx.build_coarse(dp, um, p); dcx = m["close"]; drr = dcx.pct_change(fill_method=None)
        oo = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
        scp = oo.assign(date=pd.to_datetime(oo["date"], utc=True)).pivot(index="date", columns="symbol", values="score_linear").reindex_like(dcx)
        prl, _, _ = run_book(dcx, drr, um, scp, p, step=15, hyst=0.5)
        prl.index = pd.to_datetime(prl.index).tz_localize(None).normalize()
    except Exception as e:
        print("(PRL unavailable)", e)

    def stat(d):
        d = d.dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
        cagr = eq.iloc[-1] ** (252 / max(len(d), 1)) - 1
        sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
        wk = d.resample("W").sum()
        return cagr, sh, dd, float((wk > 0).mean())

    print("\n=== risk-managed sweep-fade RETEST sleeve ===")
    print(f"  {'risk':>6} {'maxC':>5} {'CAGR':>6} {'Sharpe':>7} {'maxDD':>6} {'+wk':>5} {'corrPRL':>8}   by-year")
    best = None
    for risk_per in (0.003, 0.005, 0.01):
        for maxconc in (10, 20, 40):
            d = sleeve(risk_per, maxconc)
            cagr, sh, dd, wk = stat(d)
            cc = ""
            if prl is not None:
                a, b = d.align(prl, join="inner"); cc = f"{a.corr(b):+.2f}"
            ys = " ".join(f"{y}:{(1+g).prod()-1:+.0%}" for y, g in d.groupby(d.index.year))
            print(f"  {risk_per:>6.1%} {maxconc:>5} {cagr*100:+5.0f}% {sh:+7.2f} {dd*100:+5.0f}% {wk*100:4.0f}% {cc:>8}   {ys}")
            if best is None or sh > best[1]:
                best = (d, sh, risk_per, maxconc)

    if prl is not None:
        d = best[0]
        a, b = d.align(prl, join="inner")
        sa, sb = a.std(), b.std(); wv = (1 / sa) / (1 / sa + 1 / sb)
        comb = wv * a + (1 - wv) * b
        cagr, sh, dd, wk = stat(comb)
        ys = " ".join(f"{y}:{(1+g).prod()-1:+.0%}" for y, g in comb.groupby(comb.index.year))
        cg, ch, cd, cw = stat(b)
        print(f"\n=== PRL vs PRL + sweep-fade retest (vol-parity) ===")
        print(f"  PRL alone         Sharpe {ch:+.2f} maxDD {cd*100:+.0f}% +wk {cw*100:.0f}%")
        print(f"  PRL + sweep-fade  Sharpe {sh:+.2f} maxDD {dd*100:+.0f}% +wk {wk*100:.0f}%  [{ys}]")


if __name__ == "__main__":
    main()
