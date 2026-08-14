"""Common traits of winning vs losing TRADES and PERIODS (user 2026-08-04).

Two questions, both strengthened with CatBoost:
  (1) TRADE level  — attach entry features to every executed trade of the book,
      split winners (pnl_net>0) vs losers, find what separates them (univariate
      median gap + AUC, then a CatBoost win/lose classifier with importance).
  (2) PERIOD level — aggregate to rebalance segments, attach market-context at
      entry (BTC trend, breadth, cross-sectional dispersion, median vol), and see
      in which conditions the strategy wins vs loses.

IS only (env XSM_*). Never touches OOS.
Run: python -m anomaly_science.strategy.xsect_momentum.research.win_lose
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from anomaly_science.strategy.xsect_momentum.research import backtest as bt
from anomaly_science.strategy.xsect_momentum.research import features as ft
from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research import score as sc
from anomaly_science.strategy.xsect_momentum.research.policy import PRIMARY as P

OUT = Path(".output/results/xsect_momentum")
HORIZON = 7


def _auc(feature: np.ndarray, label: np.ndarray) -> float:
    m = np.isfinite(feature)
    f, y = feature[m], label[m]
    if y.sum() == 0 or y.sum() == len(y):
        return np.nan
    r = pd.Series(f).rank().to_numpy()
    n1 = y.sum(); n0 = len(y) - n1
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    from catboost import CatBoostClassifier

    panel = pn.load_panel(is_only=True)
    close = pn.pivot(panel, "close"); open_ = pn.pivot(panel, "open"); qv = pn.pivot(panel, "quote_volume")
    umask = pn.build_universe_mask(panel, qv, P.universe_n, P.liquidity_lb, P.min_age_days)
    qscore = sc.quality_score(panel, P, umask)

    # run the market-neutral quality book
    p = replace(P, direction_mode="market_neutral")
    res = bt.run_backtest(panel, close, open_, qv, umask, qscore, bt.sel_market_neutral, p, bidirectional=True)
    td = res.trades.copy()
    td["is_short"] = (td["weight"] < 0).astype(int)
    td["win"] = (td["pnl_net"] > 0).astype(int)
    td["rdate"] = pd.to_datetime(td["rebalance_date"], utc=True).dt.tz_convert(None)
    print(f"book trades={len(td)}  win_rate={td['win'].mean():.3f}  net=${res.equity.iloc[-1]-P.start_equity:+.0f}")

    # attach entry features
    feat_df = ft.build_features(panel, P, HORIZON)
    feat_df["date"] = pd.to_datetime(feat_df["date"], utc=True).dt.tz_convert(None)
    fcols = [c for c in ft.feature_columns(feat_df) if c != "in_universe"]
    merged = td.merge(feat_df[["date", "symbol"] + fcols], left_on=["rdate", "symbol"], right_on=["date", "symbol"], how="left")

    # --- (1a) univariate trait gap, split by side (short win = coin fell) ---
    print("\n=== TRADE traits: median(win) - median(lose) and AUC(feature->win) ===")
    for side, lab in [(0, "LONG"), (1, "SHORT")]:
        sub = merged[merged["is_short"] == side]
        print(f"\n-- {lab} trades: n={len(sub)} win_rate={sub['win'].mean():.2f} --")
        gaps = []
        for c in fcols:
            w = sub[sub["win"] == 1][c].median(); l = sub[sub["win"] == 0][c].median()
            au = _auc(sub[c].to_numpy(), sub["win"].to_numpy())
            if np.isfinite(au):
                gaps.append((c, w - l, au))
        for c, g, au in sorted(gaps, key=lambda x: -abs(x[2] - 0.5))[:8]:
            print(f"   {c:20s} win-lose_med={g:+.4g}  AUC={au:.3f}")

    # --- (1b) CatBoost win/lose classifier (strengthen) ---
    print("\n=== CatBoost win/lose classifier (time-block CV, features + is_short) ===")
    merged["date"] = merged["rdate"]
    dcols = fcols + ["is_short"]
    dates = np.array(sorted(merged["date"].unique()))
    folds = np.array_split(dates, 4)
    aucs = []; imp_acc = None
    for i in range(1, 4):
        test_d = folds[i]; train_d = dates[dates < test_d.min() - np.timedelta64(HORIZON + 2, "D")]
        tr = merged[merged["date"].isin(train_d)]; te = merged[merged["date"].isin(test_d)]
        if len(tr) < 100 or te["win"].nunique() < 2:
            continue
        clf = CatBoostClassifier(iterations=400, depth=5, learning_rate=0.03, l2_leaf_reg=8,
                                 random_seed=i, verbose=False)
        clf.fit(tr[dcols], tr["win"])
        pr = clf.predict_proba(te[dcols])[:, 1]
        aucs.append(_auc(pr, te["win"].to_numpy()))
        imp = pd.Series(clf.get_feature_importance(), index=dcols)
        imp_acc = imp if imp_acc is None else imp_acc + imp
    if aucs:
        print(f"  OOF win/lose AUC = {np.nanmean(aucs):.3f}  (0.5=no info)")
        print("  top importance:", ", ".join(f"{k}={v:.1f}" for k, v in imp_acc.sort_values(ascending=False).head(8).items()))

    # --- (2) PERIOD level: segment return vs market context ---
    print("\n=== PERIOD traits: rebalance-segment PnL vs entry market context ===")
    ret = close.pct_change(fill_method=None)
    tr7 = close / close.shift(7) - 1.0
    uni_ret = tr7.where(umask)
    breadth = (uni_ret > 0).sum(axis=1) / umask.sum(axis=1)
    dispersion = uni_ret.std(axis=1)
    dvol = ret.rolling(P.vol_lb, min_periods=10).std().where(umask).median(axis=1)
    btc = close["BTCUSDT"] if "BTCUSDT" in close else close.median(axis=1)
    btc_tr = (np.log(btc) - np.log(btc.shift(P.regime_lb)))
    ctx = pd.DataFrame({"breadth": breadth, "dispersion": dispersion, "med_vol": dvol, "btc_trend": btc_tr})
    ctx.index = pd.to_datetime(ctx.index, utc=True).tz_convert(None)

    seg = td.groupby("rdate")["pnl_net"].sum().rename("seg_pnl").reset_index()
    seg = seg.merge(ctx.reset_index().rename(columns={"index": "rdate", "date": "rdate"}), on="rdate", how="left")
    seg["win_seg"] = (seg["seg_pnl"] > 0).astype(int)
    print(f"  segments={len(seg)}  win_seg_rate={seg['win_seg'].mean():.2f}")
    for c in ["breadth", "dispersion", "med_vol", "btc_trend"]:
        w = seg[seg["win_seg"] == 1][c].median(); l = seg[seg["win_seg"] == 0][c].median()
        rho, _ = spearmanr(seg[c], seg["seg_pnl"], nan_policy="omit")
        print(f"   {c:12s} win_med={w:+.4g}  lose_med={l:+.4g}  corr(ctx,segPnL)={rho:+.3f}")

    merged.to_parquet(OUT / "win_lose_trades.parquet")
    print(f"\nwrote {OUT / 'win_lose_trades.parquet'}")


if __name__ == "__main__":
    main()
