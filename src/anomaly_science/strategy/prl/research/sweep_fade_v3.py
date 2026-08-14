"""Sweep-fade v3: risk-managed sleeve (fixed risk/trade + concurrency cap) -> smooth + Calmar.

v2: top-decile selected sweep-fade shorts are +0.186%/trade net 6bps, year-stable, and
UNCORRELATED with PRL (corr +0.05) -- but the naive equal-weight-per-day sleeve had maxDD
-87% because sweeps CLUSTER (a volatile day fires many concurrent shorts; a flyer day stops
them all out together). v3 builds a proper event-driven short book:
  - size each trade to a FIXED fractional risk (stop = sweep-high, so risk distance is known)
  - CAP total concurrent gross exposure (diversify across time, kill the cluster blow-ups)
  - daily mark-to-market -> Sharpe/Calmar/per-year/maxDD, net of cost, + corr(PRL), then a
    vol-parity combine with PRL to test the Calmar lift.
Sweep the risk/trade, concurrency cap, and selection fraction. IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.sweep_fade_v3 --n 150
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, KFAIL, MAXH, SB, TP = 20, 4, 48, 1.0, 2.0
COST = 6 / 1e4


def build_trades(n):
    panel = load_1h(_liquid(n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv = (px(c) for c in ("close", "high", "low", "quote_volume"))
    idx, cols = close.index, close.columns
    btc = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)).to_numpy()
    hi_lvl = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= hi_lvl
    poke = (high >= hi_lvl) & (~above.shift(1).fillna(False)) & hi_lvl.notna()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    vs = qv / qv.rolling(168, min_periods=48).mean()
    ema = close / close.ewm(span=168, min_periods=84).mean() - 1
    m24 = close / close.shift(24) - 1
    breadth = above.mean(axis=1).to_numpy()
    C, Hh, L, A, HL, V, EMA, M24 = (x.to_numpy() for x in (close, high, low, atr, hi_lvl, vs, ema, m24))
    pk = poke.to_numpy(); rows = []
    for si in range(len(cols)):
        for i in np.where(pk[:, si])[0]:
            if i + KFAIL + MAXH >= len(idx) or not (A[i, si] > 0):
                continue
            lv = HL[i, si]; j = None
            for t in range(i, i + KFAIL + 1):
                if C[t, si] < lv:
                    j = t; break
            if j is None:
                continue
            ext = np.nanmax(Hh[i:j + 1, si]); entry = C[j, si]; a = A[i, si]
            stop = ext + SB * a; tp = entry - TP * a
            outcome, exitpx, exitbar = "time", C[j + MAXH, si], j + MAXH
            for t in range(j + 1, j + MAXH + 1):
                if Hh[t, si] >= stop:
                    outcome, exitpx, exitbar = "stop", stop, t; break
                if L[t, si] <= tp:
                    outcome, exitpx, exitbar = "tp", tp, t; break
            r_short = (entry - exitpx) / entry                     # raw short return (no hedge)
            risk = (stop - entry) / entry                          # per-unit risk distance (>0)
            rows.append(dict(j=j, exitbar=exitbar, entry_t=idx[j], exit_t=idx[exitbar],
                             r_short=r_short, risk=risk, win=int(r_short > COST),
                             wick=(ext - lv) / a, fail_spd=j - i, reclaim=(lv - entry) / a,
                             vol_sweep=V[i, si], mom24=M24[i, si], ema=EMA[i, si],
                             atr_pct=a / entry, breadth=breadth[i]))
    return pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True), idx


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    tr, idx = build_trades(args.n)
    tr["year"] = pd.to_datetime(tr["entry_t"]).dt.year
    feats = ["wick", "fail_spd", "reclaim", "vol_sweep", "mom24", "ema", "atr_pct", "breadth"]
    print(f"sweeps: {len(tr)}  gross short exp={tr.r_short.mean()*100:+.3f}%")

    # walk-forward reverter score
    from catboost import CatBoostClassifier
    dn = pd.to_datetime(tr["entry_t"]).dt.tz_localize(None)
    oof = pd.Series(np.nan, index=tr.index)
    for tr_d, te_d in time_folds(dn.values, 5, embargo=2):
        tri = tr.index[dn.isin(tr_d)]; tei = tr.index[dn.isin(te_d)]
        if len(tri) < 300 or tr.loc[tri, "win"].nunique() < 2:
            continue
        cb = CatBoostClassifier(iterations=300, depth=5, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
        cb.fit(tr.loc[tri, feats], tr.loc[tri, "win"]); oof.loc[tei] = cb.predict_proba(tr.loc[tei, feats])[:, 1]
    tr["score"] = oof.fillna(oof.median())

    hidx = idx  # hourly grid
    hpos = {t: k for k, t in enumerate(hidx)}

    def sleeve(sel_frac, risk_per, maxconc):
        sel = tr[tr.score >= tr.score.quantile(1 - sel_frac)].copy()
        # event-driven: each trade holds units of RISK from entry->exit; cap concurrent count
        pnl_h = np.zeros(len(hidx)); active_end = []       # heap of exit positions currently open
        # simple concurrency: iterate trades in entry order, admit if open slots
        sel = sel.sort_values("entry_t")
        open_exits = []
        for _, row in sel.iterrows():
            je = hpos[row.entry_t]; xe = hpos[row.exit_t]
            open_exits = [e for e in open_exits if e > je]
            if len(open_exits) >= maxconc:
                continue
            open_exits.append(xe)
            w = risk_per / row.risk                          # size so stop-loss = risk_per of book
            pnl_h[xe] += w * (row.r_short - COST)            # realize net at exit bar
        s = pd.Series(pnl_h, index=hidx)
        daily = s.resample("1D").sum()
        return daily

    print("\n=== risk-managed sleeve sweep (risk/trade, concurrency cap, selection) ===")
    print(f"  {'sel':>6} {'risk':>6} {'maxC':>5} {'CAGR':>6} {'Sharpe':>7} {'maxDD':>6} {'corrPRL':>8}   by-year")
    # daily PRL
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
        m = fx.build_coarse(dp, um, p); dc = m["close"]; drr = dc.pct_change(fill_method=None)
        oo = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
        scp = oo.assign(date=pd.to_datetime(oo["date"], utc=True)).pivot(index="date", columns="symbol", values="score_linear").reindex_like(dc)
        prl, _, _ = run_book(dc, drr, um, scp, p, step=15, hyst=0.5)
        prl.index = pd.to_datetime(prl.index).tz_localize(None).normalize()
    except Exception as e:
        print("  (PRL unavailable)", e)

    best = None
    for sel_frac in (0.25, 0.10):
        for risk_per in (0.005, 0.01):
            for maxconc in (5, 15, 40):
                d = sleeve(sel_frac, risk_per, maxconc)
                d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
                eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
                cagr = eq.iloc[-1] ** (252 / max(len(d), 1)) - 1
                shp = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
                cc = ""
                if prl is not None:
                    a, b = d.align(prl, join="inner"); cc = f"{a.corr(b):+.2f}"
                ys = " ".join(f"{y}:{(1+g).prod()-1:+.0%}" for y, g in d.groupby(d.index.year))
                print(f"  {sel_frac:>6.0%} {risk_per:>6.1%} {maxconc:>5} {cagr*100:+5.0f}% {shp:+7.2f} {dd*100:+5.0f}% {cc:>8}   {ys}")
                if best is None or shp > best[0]:
                    best = (shp, d, sel_frac, risk_per, maxconc)

    if prl is not None and best is not None:
        _, d, sf, rp_, mc = best
        a, b = d.align(prl, join="inner")
        sa, sb = a.std(), b.std(); wv = (1 / sa) / (1 / sa + 1 / sb)
        comb = wv * a + (1 - wv) * b
        eq = (1 + comb).cumprod(); dd = float((eq / eq.cummax() - 1).min())
        shp = comb.mean() / comb.std() * np.sqrt(252)
        ys = " ".join(f"{y}:{(1+g).prod()-1:+.0%}" for y, g in comb.groupby(comb.index.year))
        print(f"\n=== best sweep-fade sleeve (sel {sf:.0%}, risk {rp_:.1%}, maxC {mc}) + PRL vol-parity ===")
        print(f"  PRL alone         Sharpe {b.mean()/b.std()*np.sqrt(252):+.2f}")
        print(f"  PRL + sweep-fade  Sharpe {shp:+.2f}  maxDD {dd*100:+.0f}%  [{ys}]")


if __name__ == "__main__":
    main()
