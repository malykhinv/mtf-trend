"""Breakout confirm/fail Stage 2 (exhaustive): can the FULL feature arsenal predict
which breakouts run? Everything we studied, applied to each 1h breakout event.

At each break of the trailing 20d high we compute (all causal): volume/trade/taker
inflow, level age & prior-breakout recurrence, extension above level, EMA positions,
ATR-normalized candle, ATH distance, recent momentum, coin-minus-BTC, market breadth
/ dispersion / BTC trend, correlation-basket confirmation (frac of daily-cluster peers
above their level), and funding. Then, separately for HELD (long) and FAILED (short):
per-year-stable separation + causal walk-forward classifier OOF AUC (win & runner) +
shuffle. IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.breakout_features --n 150
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h
from anomaly_science.strategy.prl.research.peer import build_clusters

FUND_DIR = ".output/market/binance_vision/um_futures/funding_v1"
T, H, LVL = 4, 24, 20


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv, ntr, tbq = (px(c) for c in ("close", "high", "low", "quote_volume", "number_of_trades", "taker_buy_quote_volume"))
    idx, cols = close.index, close.columns
    btc = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    ret = close.pct_change(fill_method=None)

    dhigh = high.resample("1D").max()
    level = dhigh.rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = above & (~above.shift(1).fillna(False)) & level.notna()

    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    F = {
        "ext_above": close / level - 1,
        "vsurge": qv / (_roll(qv, 168, "mean") + 1e-9),
        "trade_surge": ntr / (_roll(ntr, 168, "mean") + 1e-9),
        "taker_ratio": tbq / (qv + 1e-9),
        "ema24": close / close.ewm(span=24, min_periods=12).mean() - 1,
        "ema72": close / close.ewm(span=72, min_periods=36).mean() - 1,
        "ema168": close / close.ewm(span=168, min_periods=84).mean() - 1,
        "candle_atr": (high - low) / (atr + 1e-9),
        "dist_ath": close / close.cummax() - 1,
        "r24": close / close.shift(24) - 1,
        "r72": close / close.shift(72) - 1,
        "coin_minus_btc_24": (close / close.shift(24) - 1).sub(btc / btc.shift(24) - 1, axis=0),
        "n_brk_30d": brk.astype(float).rolling(720, min_periods=100).sum(),
        "level_age_d": (dhigh / dhigh.rolling(LVL, min_periods=LVL // 2).max()).reindex(idx, method="ffill"),
    }
    # cross-symbol context (hour series broadcast)
    breadth = above.mean(axis=1)
    disp = ret.std(axis=1)
    btc24 = (btc / btc.shift(24) - 1)
    # correlation-basket confirmation: daily clusters, frac of cluster peers above level
    dclose = close.resample("1D").last()
    deps = dclose.pct_change(fill_method=None).sub(dclose.pct_change(fill_method=None).mean(axis=1), axis=0)
    dumask = dclose.notna()
    assign, _ = build_clusters(deps, dumask, lookback=90, refit=21, k=10)
    dabove = (dclose >= dhigh.rolling(LVL, min_periods=LVL // 2).max().shift(1))
    basket = pd.DataFrame(np.nan, index=dclose.index, columns=cols)
    for t in range(len(dclose.index)):
        a = dabove.iloc[t]; c = assign.iloc[t]
        d = pd.DataFrame({"a": a.values, "c": c.values}, index=cols).dropna()
        if len(d):
            basket.iloc[t] = d.groupby("c")["a"].transform("mean").reindex(cols)
    basket_h = basket.shift(1).reindex(idx, method="ffill")

    # funding (optional)
    fu = {}
    for s in cols:
        f = f"{FUND_DIR}/{s}.parquet"
        if os.path.exists(f):
            df = pd.read_parquet(f)
            tt = pd.to_datetime(df["funding_time"], unit="ms", utc=True).dt.floor("h")
            fu[s] = df.groupby(tt)["funding_rate"].sum()
    fund = (pd.DataFrame(fu).reindex(idx).reindex(columns=cols) if fu else None)
    fund_cum = _roll(fund, 168, "sum") if fund is not None else None

    # collect events
    bn = close.to_numpy(); lv = level.to_numpy()
    bf = ((btc.shift(-(T + H)) / btc.shift(-T) - 1.0)).reindex(idx).to_numpy()
    fmats = {k: v.to_numpy() for k, v in F.items()}
    breadth_a, disp_a, btc24_a = breadth.to_numpy(), disp.to_numpy(), btc24.to_numpy()
    basket_a = basket_h.to_numpy()
    fund_a = fund_cum.to_numpy() if fund_cum is not None else None
    ci = {c: j for j, c in enumerate(cols)}
    rows = []
    bmask = brk.to_numpy()
    for si, s in enumerate(cols):
        ev = np.where(bmask[:, si])[0]
        for i in ev:
            dj, fj = i + T, i + T + H
            if fj >= len(idx) or not (bn[dj, si] > 0):
                continue
            fwd = bn[fj, si] / bn[dj, si] - 1.0
            rel = fwd - (bf[dj] if np.isfinite(bf[dj]) else 0.0)
            row = {"symbol": s, "date": idx[i], "held": bool(bn[dj, si] >= lv[i, si]),
                   "fwd_rel": rel, "breadth": breadth_a[i], "disp": disp_a[i], "btc24": btc24_a[i],
                   "basket": basket_a[i, si]}
            for k in fmats:
                row[k] = fmats[k][i, si]
            if fund_a is not None:
                row["funding"] = fund_a[i, si]
            rows.append(row)
    tr = pd.DataFrame(rows)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    feats = [c for c in tr.columns if c not in ("symbol", "date", "held", "fwd_rel", "year")]
    tr["win"] = (tr.fwd_rel > 0).astype(int)
    tr["runner"] = (tr.fwd_rel > 0.05).astype(int)
    print(f"events={len(tr)}  held={int(tr.held.sum())}  failed={int((~tr.held).sum())}  feats={len(feats)}\n")

    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score

    def wf_auc(g, target):
        dn = pd.to_datetime(g["date"]).dt.tz_localize(None)
        oof = pd.Series(np.nan, index=g.index)
        for tr_d, te_d in time_folds(dn.values, 5, embargo=2):
            tri = g.index[dn.isin(tr_d)]; tei = g.index[dn.isin(te_d)]
            if len(tri) < 200 or g.loc[tri, target].nunique() < 2:
                continue
            cb = CatBoostClassifier(iterations=250, depth=4, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
            cb.fit(g.loc[tri, feats], g.loc[tri, target]); oof.loc[tei] = cb.predict_proba(g.loc[tei, feats])[:, 1]
        m = oof.notna()
        if m.sum() < 50 or g.loc[m, target].nunique() < 2:
            return np.nan, np.nan
        auc = roc_auc_score(g.loc[m, target], oof[m])
        rng = np.random.default_rng(0)
        ysh = pd.Series(rng.permutation(g[target].values), index=g.index)
        return auc, roc_auc_score(ysh[m], oof[m])

    for side, name in ((True, "HELD (long)"), (False, "FAILED (short)")):
        g = tr[tr.held == side].reset_index(drop=True)
        print(f"=== {name}: n={len(g)}  win-rate={g.win.mean():.3f}  runner-rate={g.runner.mean():.3f} ===")
        # top separators (win)
        diffs = []
        for f in feats:
            a, b = g.loc[g.win == 1, f], g.loc[g.win == 0, f]
            sd = g[f].std()
            if sd > 0 and a.notna().sum() > 30 and b.notna().sum() > 30:
                signs = [np.sign(gy[gy.win == 1][f].mean() - gy[gy.win == 0][f].mean()) for _, gy in g.groupby("year") if gy.win.nunique() == 2]
                stable = len(signs) > 1 and all(x == signs[0] for x in signs)
                diffs.append((f, (a.mean() - b.mean()) / sd, stable))
        diffs.sort(key=lambda x: -abs(x[1]))
        print("  top win/lose separators (* year-stable): " + "  ".join(f"{f}={d:+.2f}{'*' if st else ''}" for f, d, st in diffs[:6]))
        for target in ("win", "runner"):
            auc, sh = wf_auc(g, target)
            print(f"  walk-forward OOF AUC [{target}] = {auc:.3f}  (shuffle {sh:.3f})")
        print()


if __name__ == "__main__":
    main()
