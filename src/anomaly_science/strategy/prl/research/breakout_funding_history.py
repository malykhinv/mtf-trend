"""Breakout: funding (before/at/after/dynamics) + the coin's OWN breakout history.

Two new feature families for the 20d-high breakout (user):
  A. FUNDING around the break: fnow (last rate at break), fpre3d (mean before),
     fchg3d (rising/falling), fpost (mean over the confirmation window [break,break+T]).
  B. OWN prior-breakout track record (causal -- only prior breakouts whose forward
     outcome resolved before now): n_prior, prior win-rate, prior mean forward,
     prior runner-rate. Does a coin's breakout 'personality' predict the next one?

Separation (per-year stable) + causal walk-forward AUC (win & runner) incremental over
a base (vsurge, breadth), + shuffle, separately HELD/FAILED. IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.breakout_funding_history --n 150
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

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
    close = panel.pivot(index="date", columns="symbol", values="close")
    high = panel.pivot(index="date", columns="symbol", values="high")
    qv = panel.pivot(index="date", columns="symbol", values="quote_volume")
    idx, cols = close.index, close.columns
    btc = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    btc_fwd = (btc.shift(-(T + H)) / btc.shift(-T) - 1.0).reindex(idx).to_numpy()
    level = high.resample("1D").max().rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = above & (~above.shift(1).fillna(False)) & level.notna()
    breadth = above.mean(axis=1)
    vs = (qv / (_roll(qv, 168, "mean") + 1e-9))

    # funding hourly (ffill), causal features
    fu = {}
    for s in cols:
        f = f"{FUND_DIR}/{s}.parquet"
        if os.path.exists(f):
            df = pd.read_parquet(f)
            tt = pd.to_datetime(df["funding_time"], unit="ms", utc=True).dt.floor("h")
            fu[s] = df.groupby(tt)["funding_rate"].last()
    fund = pd.DataFrame(fu).reindex(idx).ffill().reindex(columns=cols) if fu else None
    if fund is not None:
        fnow = fund.shift(1)
        fpre = _roll(fund, 72, "mean").shift(1)
        fchg = (fund - fund.shift(72)).shift(1)
        fpost = _roll(fund, T, "mean")   # mean over [i-T+1..i]; used shifted -> approximate window
        print(f"funding coverage: {int(fund.notna().any().sum())} symbols")
    else:
        print("no funding")

    Cn, Ln = close.to_numpy(), level.to_numpy()
    vsa, bra = vs.to_numpy(), breadth.to_numpy()
    F = {"fnow": fnow.to_numpy(), "fpre3d": fpre.to_numpy(), "fchg3d": fchg.to_numpy(),
         "fpost": fpost.to_numpy()} if fund is not None else {}
    bmask = brk.to_numpy()

    # collect events with outcome, then add own-history (causal) per symbol
    rows = []
    for si, s in enumerate(cols):
        evs = np.where(bmask[:, si])[0]
        hist = []  # (resolve_time_idx, fwd_rel) of prior breakouts
        for i in evs:
            dj, fj = i + T, i + T + H
            if fj >= len(idx) or not (Cn[dj, si] > 0):
                continue
            rel = (Cn[fj, si] / Cn[dj, si] - 1.0) - (btc_fwd[dj] if np.isfinite(btc_fwd[dj]) else 0.0)
            # own prior-breakout stats resolved before this event's decision (dj)
            res = [f for (rt, f) in hist if rt < i]  # resolved strictly before current break bar
            res = res[-20:]
            row = {"symbol": s, "date": idx[i], "held": bool(Cn[dj, si] >= Ln[i, si]), "fwd_rel": rel,
                   "vsurge": vsa[i, si], "breadth": bra[i],
                   "n_prior": len(res),
                   "prior_win": (np.mean([x > 0 for x in res]) if res else 0.5),
                   "prior_meanfwd": (np.mean(res) if res else 0.0),
                   "prior_runner": (np.mean([x > 0.05 for x in res]) if res else 0.0)}
            for k, mat in F.items():
                row[k] = mat[i, si]
            rows.append(row)
            hist.append((fj, rel))          # resolves at fj
    tr = pd.DataFrame(rows)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    tr["win"] = (tr.fwd_rel > 0).astype(int); tr["runner"] = (tr.fwd_rel > 0.05).astype(int)
    base = ["vsurge", "breadth"]
    fund_f = [k for k in F]
    hist_f = ["n_prior", "prior_win", "prior_meanfwd", "prior_runner"]
    print(f"events={len(tr)}  held={int(tr.held.sum())}\n")

    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score

    def auc(g, feats, target):
        dn = pd.to_datetime(g["date"]).dt.tz_localize(None)
        oof = pd.Series(np.nan, index=g.index)
        for tr_d, te_d in time_folds(dn.values, 5, embargo=2):
            tri = g.index[dn.isin(tr_d)]; tei = g.index[dn.isin(te_d)]
            if len(tri) < 200 or g.loc[tri, target].nunique() < 2:
                continue
            cb = CatBoostClassifier(iterations=250, depth=4, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
            cb.fit(g.loc[tri, feats], g.loc[tri, target]); oof.loc[tei] = cb.predict_proba(g.loc[tei, feats])[:, 1]
        mm = oof.notna()
        if mm.sum() < 50 or g.loc[mm, target].nunique() < 2:
            return np.nan
        return roc_auc_score(g.loc[mm, target], oof[mm])

    for side, nm in ((True, "HELD (long)"), (False, "FAILED (short)")):
        g = tr[tr.held == side].reset_index(drop=True)
        print(f"=== {nm}: n={len(g)} win={g.win.mean():.3f} runner={g.runner.mean():.3f} ===")
        allf = base + fund_f + hist_f
        for f in fund_f + hist_f:
            a, b = g.loc[g.win == 1, f], g.loc[g.win == 0, f]
            sd = g[f].std()
            gap = (a.mean() - b.mean()) / sd if sd > 0 else 0
            signs = [np.sign(gy[gy.win == 1][f].mean() - gy[gy.win == 0][f].mean()) for _, gy in g.groupby("year") if gy.win.nunique() == 2]
            st = len(signs) > 1 and all(x == signs[0] for x in signs)
            print(f"    sep {f:14s} {gap:+.3f}{'*' if st else ''}")
        for target in ("win", "runner"):
            ab = auc(g, base, target); af = auc(g, base + fund_f, target); ah = auc(g, base + hist_f, target); aa = auc(g, allf, target)
            print(f"    AUC[{target}] base={ab:.3f}  +funding={af:.3f}  +history={ah:.3f}  +both={aa:.3f}")
        print()


if __name__ == "__main__":
    main()
