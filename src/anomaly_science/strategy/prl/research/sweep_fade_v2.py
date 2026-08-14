"""Sweep-fade v2: select REVERTERS vs FLYERS, net of costs, and is it uncorrelated with PRL?

v1: upside sweep fade-short with a 2xATR target is +0.16%/trade gross and year-stable, but that
is ~breakeven after costs with 22.9k high-turnover trades. v2 tries to convert it to a real edge:
  1. FEATURES at the sweep (wick overshoot, reclaim speed/depth, sweep volume, coin momentum,
     extension, ATR, funding, breadth) -> walk-forward CatBoost REVERTER classifier.
  2. SELECT the top-scored sweeps -> does expectancy rise enough to beat costs (3/6/10 bps)?
  3. Build a daily sleeve from the selected fades -> Sharpe/Calmar/per-year + CORRELATION with the
     daily PRL book (an uncorrelated short-reversion stream would lift the Calmar ceiling).
IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.sweep_fade_v2 --n 150
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, KFAIL, MAXH, SB, TP = 20, 4, 48, 1.0, 2.0


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv = (px(c) for c in ("close", "high", "low", "quote_volume"))
    idx, cols = close.index, close.columns
    btc = (close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)).to_numpy()
    hi_lvl = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= hi_lvl
    poke = (high >= hi_lvl) & (~above.shift(1).fillna(False)) & hi_lvl.notna()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    breadth = above.mean(axis=1)
    vs = qv / qv.rolling(168, min_periods=48).mean()
    ema = close / close.ewm(span=168, min_periods=84).mean() - 1
    m24 = close / close.shift(24) - 1
    C, Hh, L, A, HL, V, BR, EMA, M24 = (x.to_numpy() for x in (close, high, low, atr, hi_lvl, vs, breadth if False else vs, ema, m24))
    BRn = breadth.to_numpy()
    pk = poke.to_numpy()
    rows = []
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
            pnl = (btc[exitbar] / btc[j] - 1.0) - (exitpx / entry - 1.0)   # market-relative short PnL
            rows.append(dict(date=idx[j], si=si, pnl=pnl, win=int(pnl > 0),
                             wick=(ext - lv) / a, fail_spd=j - i, reclaim=(lv - entry) / a,
                             vol_sweep=V[i, si], mom24=M24[i, si], ema=EMA[i, si],
                             atr_pct=a / entry, breadth=BRn[i]))
    tr = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan).dropna().reset_index(drop=True)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    feats = ["wick", "fail_spd", "reclaim", "vol_sweep", "mom24", "ema", "atr_pct", "breadth"]
    print(f"\nsweeps: {len(tr)}  gross exp={tr.pnl.mean()*100:+.3f}%  win={tr.win.mean():.1%}")

    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score
    dn = pd.to_datetime(tr["date"]).dt.tz_localize(None)
    oof = pd.Series(np.nan, index=tr.index)
    for tr_d, te_d in time_folds(dn.values, 5, embargo=2):
        tri = tr.index[dn.isin(tr_d)]; tei = tr.index[dn.isin(te_d)]
        if len(tri) < 300 or tr.loc[tri, "win"].nunique() < 2:
            continue
        cb = CatBoostClassifier(iterations=300, depth=5, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
        cb.fit(tr.loc[tri, feats], tr.loc[tri, "win"]); oof.loc[tei] = cb.predict_proba(tr.loc[tei, feats])[:, 1]
    tr["score"] = oof
    mm = oof.notna()
    print(f"reverter AUC = {roc_auc_score(tr.loc[mm,'win'], oof[mm]):.3f}")

    print("\n=== expectancy by selected top-fraction (net of round-trip cost) ===")
    print(f"  {'select':>10} {'n':>6} {'grossExp':>9} " + " ".join(f'net{c}bps'.rjust(9) for c in (3, 6, 10)) + "   by-year gross")
    g = tr[mm].copy()
    for frac in (1.0, 0.5, 0.25, 0.1):
        thr = g.score.quantile(1 - frac); sel = g[g.score >= thr]
        ge = sel.pnl.mean()
        nets = [(ge - c / 1e4) * 100 for c in (3, 6, 10)]
        ys = " ".join(f"{y}:{gg.pnl.mean()*100:+.2f}" for y, gg in sel.groupby("year"))
        print(f"  {f'top {frac:.0%}':>10} {len(sel):>6} {ge*100:+9.3f} " + " ".join(f"{v:+9.3f}" for v in nets) + f"   [{ys}]")

    # daily sleeve from top-25% selected fades (equal-weight per day), net 6bps
    sel = g[g.score >= g.score.quantile(0.75)].copy()
    sel["d"] = pd.to_datetime(sel["date"]).dt.tz_localize(None).dt.normalize()
    daily = (sel.assign(net=sel.pnl - 6 / 1e4).groupby("d")["net"].mean())
    daily = daily.reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D")).fillna(0.0)
    eq = (1 + daily).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    sh = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else np.nan
    print(f"\n=== sweep-fade daily sleeve (top-25%, net 6bps) ===")
    print(f"  Sharpe={sh:+.2f} maxDD={dd*100:+.0f}% "
          + " ".join(f"{y}:{(1+gg).prod()-1:+.0%}" for y, gg in daily.groupby(daily.index.year)))

    # correlation with daily PRL
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
        a, b = daily.align(prl, join="inner")
        print(f"  corr(sweep-fade sleeve, PRL daily) = {a.corr(b):+.2f}")
    except Exception as e:
        print("  PRL corr unavailable:", e)


if __name__ == "__main__":
    main()
