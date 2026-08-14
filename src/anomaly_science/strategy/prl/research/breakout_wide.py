"""Breakout WIDE feature grid (~50 causal features) -> can anything predict win/runner?

Everything, per 20d-high breakout event: momentum (7 windows), EMA position (5),
ATR/candle, level extension/age, ATH/ATL, volume/trade/taker/flow, funding
(level/change/post), own prior-breakout history, market/regime/breadth/dispersion/
BTC/ETH, correlation-basket confirmation, and post-event confirmation-window dynamics.
Walk-forward AUC (win & runner) separately HELD/FAILED, feature importance, univariate
leak-sniff, per-year, shuffle. IS only, OOS reserved.

Run: python -m anomaly_science.strategy.prl.research.breakout_wide --n 150
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
LVL, T, H = 20, 4, 24


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
    ret = close.pct_change(fill_method=None)
    btc = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    eth = close["ETHUSDT"] if "ETHUSDT" in cols else btc
    btc_fwd = (btc.shift(-(T + H)) / btc.shift(-T) - 1.0).reindex(idx).to_numpy()
    level = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = above & (~above.shift(1).fillna(False)) & level.notna()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    signed = 2 * tbq - qv

    F = {}
    for w in (1, 3, 6, 12, 24, 72, 168):
        F[f"ret{w}"] = close / close.shift(w) - 1
    for sp in (12, 24, 72, 168, 480):
        F[f"ema{sp}"] = close / close.ewm(span=sp, min_periods=sp // 2).mean() - 1
    F["ext_above"] = close / level - 1
    F["dist_ath"] = close / close.cummax() - 1
    F["dist_atl"] = close / close.cummin() - 1
    F["candle_atr"] = (high - low) / (atr + 1e-9)
    F["last3_atr"] = _roll((high - low) / (atr + 1e-9), 3, "mean")
    F["stretch"] = close / close.ewm(span=168, min_periods=84).mean() - 1
    up = ret.clip(lower=0); F["verticality"] = _roll(up, 24, "max") / (_roll(up, 24, "sum") + 1e-9)
    F["level_age"] = (close / close.rolling(480, min_periods=100).max().shift(1))
    F["vsurge24"] = qv / (_roll(qv, 24, "mean") + 1e-9)
    F["vsurge168"] = qv / (_roll(qv, 168, "mean") + 1e-9)
    F["trade_surge"] = ntr / (_roll(ntr, 168, "mean") + 1e-9)
    F["taker_ratio"] = tbq / (qv + 1e-9)
    F["signed_z"] = (signed - _roll(signed, 168, "mean")) / (_roll(signed, 168, "std") + 1e-9)
    F["cum_flow"] = _roll(signed, 24, "sum") / (_roll(qv, 24, "sum") + 1e-9)
    F["avg_trade"] = qv / ntr.replace(0, np.nan)
    F["vol_accel"] = _roll(qv, 6, "mean") / (_roll(qv, 72, "mean") + 1e-9)
    # market/regime (broadcast)
    breadth = above.mean(axis=1); disp = ret.where(above.notna()).std(axis=1)
    mvol = ret.mean(axis=1).rolling(20).std()
    for name, ser in (("breadth", breadth), ("disp", disp), ("mvol", mvol),
                      ("mkt24", ret.mean(axis=1).rolling(24).sum()),
                      ("btc24", btc / btc.shift(24) - 1), ("btc72", btc / btc.shift(72) - 1),
                      ("btc168", btc / btc.shift(168) - 1), ("eth24", eth / eth.shift(24) - 1)):
        F[name] = pd.DataFrame(np.repeat(ser.to_numpy()[:, None], len(cols), axis=1), index=idx, columns=cols)
    # funding
    fu = {}
    for s in cols:
        f = f"{FUND_DIR}/{s}.parquet"
        if os.path.exists(f):
            df = pd.read_parquet(f); tt = pd.to_datetime(df["funding_time"], unit="ms", utc=True).dt.floor("h")
            fu[s] = df.groupby(tt)["funding_rate"].last()
    if fu:
        fund = pd.DataFrame(fu).reindex(idx).ffill().reindex(columns=cols)
        F["fnow"] = fund; F["fpre3d"] = _roll(fund, 72, "mean"); F["fpre7d"] = _roll(fund, 168, "mean")
        F["fchg3d"] = fund - fund.shift(72); F["fchg7d"] = fund - fund.shift(168)
    # daily-cluster basket confirmation
    dclose = close.resample("1D").last(); dh = high.resample("1D").max()
    deps = dclose.pct_change(fill_method=None).sub(dclose.pct_change(fill_method=None).mean(axis=1), axis=0)
    assign, _ = build_clusters(deps, dclose.notna(), lookback=90, refit=21, k=10)
    dabove = (dclose >= dh.rolling(LVL, min_periods=LVL // 2).max().shift(1))
    bk = pd.DataFrame(np.nan, index=dclose.index, columns=cols)
    for t in range(len(dclose.index)):
        d = pd.DataFrame({"a": dabove.iloc[t].values, "c": assign.iloc[t].values}, index=cols).dropna()
        if len(d):
            bk.iloc[t] = d.groupby("c")["a"].transform("mean").reindex(cols)
    F["basket"] = bk.shift(1).reindex(idx, method="ffill")

    feats = list(F.keys())
    FM = {k: v.reindex(index=idx, columns=cols).to_numpy() for k, v in F.items()}
    Cn, Ln, An = close.to_numpy(), level.to_numpy(), atr.to_numpy()
    bmask = brk.to_numpy()
    rows = []
    post_names = ["pw_bars_above", "pw_min_pullback", "pw_close_pos", "pw_vol_persist", "pw_ret"]
    for si, s in enumerate(cols):
        hist = []
        for i in np.where(bmask[:, si])[0]:
            dj, fj = i + T, i + T + H
            if fj >= len(idx) or not (Cn[dj, si] > 0) or not (An[i, si] > 0):
                continue
            rel = (Cn[fj, si] / Cn[dj, si] - 1.0) - (btc_fwd[dj] if np.isfinite(btc_fwd[dj]) else 0.0)
            res = [f for (rt, f) in hist if rt < i][-20:]
            cw = Cn[i:dj + 1, si]; lw = low.to_numpy()[i:dj + 1, si]; vw = qv.to_numpy()[i:dj + 1, si]
            rng = cw.max() - lw.min()
            row = {"symbol": s, "date": idx[i], "held": bool(Cn[dj, si] >= Ln[i, si]), "fwd_rel": rel,
                   "n_prior": len(res), "prior_win": (np.mean([x > 0 for x in res]) if res else 0.5),
                   "prior_meanfwd": (np.mean(res) if res else 0.0), "prior_runner": (np.mean([x > 0.05 for x in res]) if res else 0.0),
                   "pw_bars_above": float((cw >= Ln[i, si]).mean()), "pw_min_pullback": lw.min() / Ln[i, si] - 1,
                   "pw_close_pos": (cw[-1] - lw.min()) / (rng + 1e-9), "pw_vol_persist": vw[1:].mean() / (vw[0] + 1e-9),
                   "pw_ret": cw[-1] / cw[0] - 1}
            for k in feats:
                row[k] = FM[k][i, si]
            rows.append(row); hist.append((fj, rel))
    tr = pd.DataFrame(rows)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    tr["win"] = (tr.fwd_rel > 0).astype(int); tr["runner"] = (tr.fwd_rel > 0.05).astype(int)
    allf = feats + ["n_prior", "prior_win", "prior_meanfwd", "prior_runner"] + post_names
    print(f"events={len(tr)}  held={int(tr.held.sum())}  features={len(allf)}\n")

    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score

    def wf(g, target, feats_):
        dn = pd.to_datetime(g["date"]).dt.tz_localize(None)
        oof = pd.Series(np.nan, index=g.index); imps = []
        for tr_d, te_d in time_folds(dn.values, 5, embargo=2):
            tri = g.index[dn.isin(tr_d)]; tei = g.index[dn.isin(te_d)]
            if len(tri) < 200 or g.loc[tri, target].nunique() < 2:
                continue
            cb = CatBoostClassifier(iterations=300, depth=5, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
            cb.fit(g.loc[tri, feats_], g.loc[tri, target]); oof.loc[tei] = cb.predict_proba(g.loc[tei, feats_])[:, 1]
            imps.append(cb.get_feature_importance())
        mm = oof.notna()
        auc = roc_auc_score(g.loc[mm, target], oof[mm]) if mm.sum() > 50 and g.loc[mm, target].nunique() == 2 else np.nan
        rng = np.random.default_rng(0); ysh = pd.Series(rng.permutation(g[target].values), index=g.index)
        sh = roc_auc_score(ysh[mm], oof[mm]) if mm.sum() > 50 else np.nan
        imp = pd.Series(np.mean(imps, axis=0), index=feats_).sort_values(ascending=False) if imps else pd.Series(dtype=float)
        return auc, sh, imp

    for side, nm in ((True, "HELD (long)"), (False, "FAILED (short)")):
        g = tr[tr.held == side].reset_index(drop=True)
        print(f"=== {nm}: n={len(g)} win={g.win.mean():.3f} runner={g.runner.mean():.3f} ===")
        for target in ("win", "runner"):
            auc, sh, imp = wf(g, target, allf)
            print(f"  AUC[{target}] = {auc:.3f} (shuffle {sh:.3f})  top: " + ", ".join(f"{k}={v:.1f}" for k, v in imp.head(8).items()))
        print()


if __name__ == "__main__":
    main()
