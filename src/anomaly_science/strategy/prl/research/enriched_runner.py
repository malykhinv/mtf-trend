"""A: fold LEVEL-FORMATION features into the runner-sizing model -> better sleeve Calmar?

The level-quality grid found formation features (trend_in, retr_after, vol_form, d_mid, slope_in,
appr_mom) strengthen the RUNNER (size) predictor. Here we rebuild the runner-sized breakout sleeve
with an ENRICHED runner model (base + formation) vs the BASE model, and check whether better runner
prediction improves the sleeve's Calmar and the combined (PRL + sleeve) Calmar -- the binding
constraint. IS only, OOS reserved. Run: python -m anomaly_science.strategy.prl.research.enriched_runner --n 150
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research.anatomy import run_book
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h
from anomaly_science.strategy.prl.research.regime_runner_book import sleeve_returns

LVL, T, H, W = 20, 4, 24, 480


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def cal_pyd(d):
    d = d.dropna(); eq = (1 + d).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    cagr = eq.iloc[-1] ** (252 / len(d)) - 1
    pyd = {int(y): float(((1 + g).cumprod() / (1 + g).cumprod().cummax() - 1).min()) for y, g in d.groupby(d.index.year)}
    return cagr, dd, (cagr / abs(dd) if dd < 0 else np.nan), min(pyd.values())


def line(nm, d):
    c, dd, cal, wp = cal_pyd(d)
    print(f"  {nm:28s} CAGR={c*100:+4.0f}% maxDD={dd*100:+4.0f}% Calmar={cal:4.1f} worstYrDD={wp*100:+.0f}%")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv, ntr = (px(c) for c in ("close", "high", "low", "quote_volume", "number_of_trades"))
    idx, cols = close.index, close.columns
    ret = close.pct_change(fill_method=None)
    btc = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    btc_fwd = (btc.shift(-(T + H)) / btc.shift(-T) - 1.0).to_numpy()
    level = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = (above & (~above.shift(1).fillna(False)) & level.notna()).to_numpy()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    breadth = above.mean(axis=1)
    breadth_rank = breadth.expanding(min_periods=200).apply(lambda x: (x.iloc[-1] >= x).mean(), raw=False).shift(1).reindex(idx)

    # base runner features
    B = {"ret168": close / close.shift(168) - 1, "ret24": close / close.shift(24) - 1,
         "ema480": close / close.ewm(span=480, min_periods=240).mean() - 1,
         "vsurge168": qv / (_roll(qv, 168, "mean") + 1e-9),
         "avg_trade": qv / ntr.replace(0, np.nan), "ext_above": close / level - 1}
    bm = {k: v.reindex(index=idx, columns=cols).to_numpy() for k, v in B.items()}
    C, Hh, L, V, A = (x.to_numpy() for x in (close, high, low, qv, atr))
    Ln = level.to_numpy()

    rows = []
    for si in range(len(cols)):
        for i in np.where(brk[:, si])[0]:
            dj, fj = i + T, i + T + H
            if i < W + 48 or fj >= len(idx) or not (C[dj, si] > 0) or not (A[i, si] > 0) or not (C[dj, si] >= Ln[i, si]):
                continue
            f = i - W + int(np.nanargmax(Hh[i - W:i, si]))
            if f - 48 < 0:
                continue
            lvv = Hh[f, si]
            rel = (C[fj, si] / C[dj, si] - 1.0) - (btc_fwd[dj] if np.isfinite(btc_fwd[dj]) else 0.0)
            row = {"si": si, "dj": dj, "date": idx[i], "runner": int(rel > 0.05),
                   # formation features
                   "trend_in": C[f, si] / (C[max(f - 240, 0), si] + 1e-9) - 1.0,
                   "slope_in": C[f, si] / (C[f - 48, si] + 1e-9) - 1.0,
                   "imp_atr": (Hh[f, si] - np.nanmin(L[f - 48:f + 1, si])) / (A[f, si] + 1e-9),
                   "retr_after": (lvv - np.nanmin(L[f:i, si])) / (lvv + 1e-9),
                   "vol_form": V[f, si] / (np.nanmedian(V[max(f - 24, 0):f + 24, si]) + 1e-9),
                   "d_mid": C[i, si] / ((np.nanmax(Hh[i - W:i, si]) + np.nanmin(L[i - W:i, si])) / 2 + 1e-9) - 1.0,
                   "appr_mom": C[i, si] / (C[i - 48, si] + 1e-9) - 1.0}
            for k in B:
                row[k] = bm[k][i, si]
            rows.append(row)
    ev = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
    base_f = list(B.keys())
    form_f = ["trend_in", "slope_in", "imp_atr", "retr_after", "vol_form", "d_mid", "appr_mom"]
    print(f"events={len(ev)} runner-rate={ev.runner.mean():.3f}")

    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score
    dn = pd.to_datetime(ev["date"]).dt.tz_localize(None)
    def oof_runner(feats):
        oof = pd.Series(np.nan, index=ev.index)
        for tr_d, te_d in time_folds(dn.values, 5, embargo=2):
            tri = ev.index[dn.isin(tr_d)]; tei = ev.index[dn.isin(te_d)]
            if len(tri) < 200 or ev.loc[tri, "runner"].nunique() < 2:
                continue
            cb = CatBoostClassifier(iterations=300, depth=5, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
            cb.fit(ev.loc[tri, feats], ev.loc[tri, "runner"]); oof.loc[tei] = cb.predict_proba(ev.loc[tei, feats])[:, 1]
        return oof
    oof_base = oof_runner(base_f); oof_enr = oof_runner(base_f + form_f)
    mm = oof_base.notna() & oof_enr.notna()
    print(f"runner AUC base={roc_auc_score(ev.loc[mm,'runner'], oof_base[mm]):.3f}  "
          f"enriched={roc_auc_score(ev.loc[mm,'runner'], oof_enr[mm]):.3f}")

    # build sleeves with each runner score
    ev_b = ev.copy(); ev_b["rscore"] = oof_base.fillna(oof_base.mean())
    ev_e = ev.copy(); ev_e["rscore"] = oof_enr.fillna(oof_enr.mean())
    sl_b = sleeve_returns(idx, cols, ret, ev_b, breadth_rank, "runner", "flat")
    sl_e = sleeve_returns(idx, cols, ret, ev_e, breadth_rank, "runner", "flat")

    # daily PRL + combine
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    dp = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100); dqv = pn.pivot(dp, "quote_volume")
    um = pn.build_universe_mask(dp, dqv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(dp, um, p); dc = m["close"]; dr = dc.pct_change(fill_method=None)
    oof = pd.read_parquet(".output/results/prl_coarse/oof_scores_prl.parquet")
    sc = oof.assign(date=pd.to_datetime(oof["date"], utc=True)).pivot(index="date", columns="symbol", values="score_linear").reindex_like(dc)
    prl, _, _ = run_book(dc, dr, um, sc, p, step=15, hyst=0.5)

    def norm(s):
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None).normalize(); return s.groupby(s.index).sum()
    prl, sl_b, sl_e = map(norm, (prl, sl_b, sl_e))
    def vp(a, b):
        al = pd.concat([a, b], axis=1).dropna(); inv = 1 / al.std(); return (al * (inv / inv.sum())).sum(axis=1)
    def vt(d, lb=20, cap=3.0):
        v = d.rolling(lb, min_periods=lb // 2).std().shift(1); scl = (1 / v.replace(0, np.nan)); scl = (scl / scl.mean()).clip(upper=cap).fillna(1.0); return d * scl

    print("\n=== sleeve standalone & combined (base vs enriched runner sizing) ===")
    line("breakout runner BASE", sl_b); line("breakout runner ENRICHED", sl_e)
    line("PRL + BASE vp +VT", vt(vp(prl, sl_b))); line("PRL + ENRICHED vp +VT", vt(vp(prl, sl_e)))


if __name__ == "__main__":
    main()
