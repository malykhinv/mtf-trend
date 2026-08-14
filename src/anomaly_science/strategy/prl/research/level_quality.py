"""LEVEL-QUALITY grid: characterize the BROKEN LEVEL itself, not just the break state.

User hypothesis (2026-08-14): a 20d-high on a downtrend is NOT the same as a level built by
a big sweeping rise+retrace, nor a flat consolidation. Direction may become predictable once
we describe HOW THE LEVEL FORMED and its context. We locate the formation bar (the prior high
that set the level) and compute a large market-logical metric table:

  FORMATION: volume that made the level (+ its local surge), impulse size (ATR) that made the
    high, slope INTO the high (rise-impulse vs downtrend-high), retrace depth after formation,
    level age, #tests/touches, fraction of time spent below the level since it formed.
  POSITION:  distance from ATH / ATL / mid-range, percentile within range, long trend into level.
  APPROACH:  vol compression before the break, approach momentum, breakout-bar 1h volume vs the
    formation volume.
  ABSOLUTE:  daily / weekly / monthly USDT turnover (log) -- absolute-size filter, not relative.
  ARCHETYPES: downtrend-high / impulse+retrace / consolidation -> win & runner breakdown.

Big univariate separation table (win-sep + year-stability + runner-sep) + walk-forward AUC
(direction & runner) + importances + archetype / abs-volume breakdowns. IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.level_quality --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL, T, H, W = 20, 4, 24, 480      # 20d level, 4h decision, 24h fwd, 480h (~20d) formation lookback


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv = (px(c) for c in ("close", "high", "low", "quote_volume"))
    idx, cols = close.index, close.columns
    btc = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    btc_fwd = (btc.shift(-(T + H)) / btc.shift(-T) - 1.0).to_numpy()
    level = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = (above & (~above.shift(1).fillna(False)) & level.notna()).to_numpy()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()

    C, Hh, L, V, A = (x.to_numpy() for x in (close, high, low, qv, atr))
    Ln = level.to_numpy()
    ath = close.cummax().to_numpy(); atl = close.cummin().to_numpy()
    vmed168 = qv.rolling(168, min_periods=48).median().to_numpy()

    rows = []
    for si in range(len(cols)):
        ev = np.where(brk[:, si])[0]
        for i in ev:
            dj, fj = i + T, i + T + H
            if i < W + 48 or fj >= len(idx) or not (C[dj, si] > 0) or not (A[i, si] > 0):
                continue
            lv = Ln[i, si]
            f = i - W + int(np.nanargmax(Hh[i - W:i, si]))       # formation bar (prior high maker)
            if f - 48 < 0:
                continue
            lvv = Hh[f, si]
            # formation character
            vol_form = V[f, si] / (np.nanmedian(V[max(f - 24, 0):f + 24, si]) + 1e-9)
            vol_form_abs = np.log10(V[f, si] + 1.0)
            imp_atr = (Hh[f, si] - np.nanmin(L[f - 48:f + 1, si])) / (A[f, si] + 1e-9)
            slope_in = C[f, si] / (C[f - 48, si] + 1e-9) - 1.0       # >0 rise-into-high, <0 downtrend-high
            trend_in = C[f, si] / (C[max(f - 240, 0), si] + 1e-9) - 1.0
            retr_after = (lvv - np.nanmin(L[f:i, si])) / (lvv + 1e-9)   # pullback depth since formation
            age = (i - f)
            tol = 0.003
            n_tests = int(np.sum(Hh[f:i, si] >= lvv * (1 - tol)))
            frac_below = float(np.mean(C[f:i, si] < lvv))
            # position
            wlo = np.nanmin(L[i - W:i, si]); whi = np.nanmax(Hh[i - W:i, si])
            range_pos = (C[i, si] - wlo) / (whi - wlo + 1e-9)
            d_ath = C[i, si] / (ath[i, si] + 1e-9) - 1.0
            d_atl = C[i, si] / (atl[i, si] + 1e-9) - 1.0
            d_mid = C[i, si] / ((whi + wlo) / 2 + 1e-9) - 1.0
            # approach
            r = np.diff(np.log(C[i - 96:i + 1, si] + 1e-12))
            compress = (np.nanstd(r[-48:]) + 1e-12) / (np.nanstd(r[:48]) + 1e-12)
            appr_mom = C[i, si] / (C[i - 48, si] + 1e-9) - 1.0
            brk_vol = V[i, si] / (vmed168[i, si] + 1e-9)
            brk_vs_form = (V[i, si] + 1e-9) / (V[f, si] + 1e-9)
            # absolute turnover
            vol_d = np.log10(np.nansum(V[i - 24:i, si]) + 1.0)
            vol_w = np.log10(np.nansum(V[i - 168:i, si]) + 1.0)
            vol_m = np.log10(np.nansum(V[max(i - 720, 0):i, si]) + 1.0)
            # outcome
            rel = (C[fj, si] / C[dj, si] - 1.0) - (btc_fwd[dj] if np.isfinite(btc_fwd[dj]) else 0.0)
            rows.append(dict(
                date=idx[i], held=bool(C[dj, si] >= lv), fwd=rel,
                vol_form=vol_form, vol_form_abs=vol_form_abs, imp_atr=imp_atr, slope_in=slope_in,
                trend_in=trend_in, retr_after=retr_after, age=age, n_tests=n_tests, frac_below=frac_below,
                range_pos=range_pos, d_ath=d_ath, d_atl=d_atl, d_mid=d_mid, compress=compress,
                appr_mom=appr_mom, brk_vol=brk_vol, brk_vs_form=brk_vs_form,
                vol_d=vol_d, vol_w=vol_w, vol_m=vol_m))
    tr = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    tr["win"] = (tr.fwd > 0).astype(int); tr["runner"] = (tr.fwd > 0.05).astype(int)
    feats = [c for c in tr.columns if c not in ("date", "held", "fwd", "year", "win", "runner")]
    g = tr[tr.held].reset_index(drop=True)              # confirmed-hold long side
    print(f"\nconfirmed-hold events: {len(g)}  win={g.win.mean():.3f} runner={g.runner.mean():.3f}\n")

    # big univariate separation table
    print("=== univariate LEVEL-QUALITY separation (win & runner; * = sign-stable across years) ===")
    print(f"  {'feature':14s} {'winSep':>8} {'stab':>5} {'runSep':>8} {'medFwd_hi':>10} {'medFwd_lo':>10}")
    def sep(col, tgt):
        a, b = g.loc[g[tgt] == 1, col], g.loc[g[tgt] == 0, col]
        sd = g[col].std(); return (a.mean() - b.mean()) / sd if sd > 0 else 0.0
    tabl = []
    for c in feats:
        ws = sep(c, "win"); rs = sep(c, "runner")
        signs = [np.sign(sep_y) for _, gy in g.groupby("year")
                 for sep_y in [((gy.loc[gy.win == 1, c].mean() - gy.loc[gy.win == 0, c].mean()))] if gy.win.nunique() == 2]
        stab = len(signs) > 1 and all(s == signs[0] for s in signs)
        hi = g[g[c] > g[c].median()].fwd.median() * 100; lo = g[g[c] <= g[c].median()].fwd.median() * 100
        tabl.append((c, ws, stab, rs, hi, lo))
    for c, ws, stab, rs, hi, lo in sorted(tabl, key=lambda x: -abs(x[1])):
        print(f"  {c:14s} {ws:+8.3f} {'*' if stab else ' ':>5} {rs:+8.3f} {hi:+10.2f} {lo:+10.2f}")

    # walk-forward AUC
    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score
    dn = pd.to_datetime(g["date"]).dt.tz_localize(None)
    def wf(tgt):
        oof = pd.Series(np.nan, index=g.index); imps = []
        for tr_d, te_d in time_folds(dn.values, 5, embargo=2):
            tri = g.index[dn.isin(tr_d)]; tei = g.index[dn.isin(te_d)]
            if len(tri) < 200 or g.loc[tri, tgt].nunique() < 2:
                continue
            cb = CatBoostClassifier(iterations=300, depth=5, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
            cb.fit(g.loc[tri, feats], g.loc[tri, tgt]); oof.loc[tei] = cb.predict_proba(g.loc[tei, feats])[:, 1]
            imps.append(cb.get_feature_importance())
        mm = oof.notna()
        rng = np.random.default_rng(0); ysh = pd.Series(rng.permutation(g[tgt].values), index=g.index)
        auc = roc_auc_score(g.loc[mm, tgt], oof[mm]); sh = roc_auc_score(ysh[mm], oof[mm])
        imp = pd.Series(np.mean(imps, axis=0), index=feats).sort_values(ascending=False)
        return auc, sh, imp
    print("\n=== walk-forward AUC (level-quality features only) ===")
    for tgt in ("win", "runner"):
        auc, sh, imp = wf(tgt)
        print(f"  AUC[{tgt}] = {auc:.3f} (shuffle {sh:.3f})  top: " + ", ".join(f"{k}={v:.1f}" for k, v in imp.head(10).items()))

    # archetype breakdown
    print("\n=== level ARCHETYPE breakdown (win / runner / medFwd) ===")
    g2 = g.copy()
    arch = pd.Series("other", index=g2.index)
    arch[(g2.slope_in < 0) & (g2.trend_in < 0)] = "downtrend-high"
    arch[(g2.imp_atr > g2.imp_atr.median()) & (g2.retr_after > g2.retr_after.median()) & (g2.slope_in > 0)] = "impulse+retrace"
    arch[(g2.imp_atr <= g2.imp_atr.median()) & (g2.n_tests >= g2.n_tests.median()) & (g2.retr_after <= g2.retr_after.median())] = "consolidation"
    for a, gg in g2.groupby(arch):
        print(f"  {a:16s} n={len(gg):5d}  win={gg.win.mean():.3f}  runner={gg.runner.mean():.3f}  "
              f"medFwd={gg.fwd.median()*100:+.2f}%  meanFwd={gg.fwd.mean()*100:+.2f}%")

    # absolute-volume buckets
    print("\n=== absolute WEEKLY turnover buckets (win / runner / medFwd) ===")
    g2["vbucket"] = pd.qcut(g2.vol_w, 4, labels=["Q1-low", "Q2", "Q3", "Q4-high"], duplicates="drop")
    for a, gg in g2.groupby("vbucket", observed=True):
        print(f"  {str(a):10s} n={len(gg):5d}  win={gg.win.mean():.3f}  runner={gg.runner.mean():.3f}  "
              f"medFwd={gg.fwd.median()*100:+.2f}%  meanFwd={gg.fwd.mean()*100:+.2f}%")


if __name__ == "__main__":
    main()
