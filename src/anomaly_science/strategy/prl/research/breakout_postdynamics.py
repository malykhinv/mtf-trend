"""Does the POST-EVENT dynamics (how price/volume/flow behave right after the break)
predict the outcome, where static pre-event features could not? (user)

For each 20d-high breakout we read the confirmation window [break, break+T] -- retest
depth, consolidation tightness, close position in range, hold persistence, volume
persistence, taker flow, post drift -- and ask whether these predict the forward
win/runner over [break+T, break+T+H], on top of pre-event features. Causal (window
strictly before the target), walk-forward + shuffle, separate HELD/FAILED. IS only.

Run: python -m anomaly_science.strategy.prl.research.breakout_postdynamics --T 4 --H 24
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

LVL = 20


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--T", type=int, default=4)
    ap.add_argument("--H", type=int, default=24)
    args = ap.parse_args()
    T, H = args.T, args.H
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    close = panel.pivot(index="date", columns="symbol", values="close")
    high = panel.pivot(index="date", columns="symbol", values="high")
    low = panel.pivot(index="date", columns="symbol", values="low")
    qv = panel.pivot(index="date", columns="symbol", values="quote_volume")
    tbq = panel.pivot(index="date", columns="symbol", values="taker_buy_quote_volume")
    idx, cols = close.index, close.columns
    btc = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    btc_fwd = (btc.shift(-(T + H)) / btc.shift(-T) - 1.0).reindex(idx).to_numpy()
    dhigh = high.resample("1D").max()
    level = dhigh.rolling(LVL, min_periods=LVL // 2).max().shift(1).reindex(idx, method="ffill")
    above = close >= level
    brk = above & (~above.shift(1).fillna(False)) & level.notna()
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    # pre-event features
    PRE = {"cap": _roll(qv, 720, "median").rank(axis=1, pct=True),
           "vsurge": qv / (_roll(qv, 168, "mean") + 1e-9),
           "rvol24": _roll(qv, 24, "sum") / (_roll(qv, 720, "mean") * 24 + 1e-9),
           "ema72": close / close.ewm(span=72, min_periods=36).mean() - 1,
           "r72": close / close.shift(72) - 1,
           "ext_above": close / level - 1}
    prem = {k: v.to_numpy() for k, v in PRE.items()}
    C, Hi, Lo, Q, TB, A = (x.to_numpy() for x in (close, high, low, qv, tbq, atr))
    lv = level.to_numpy()
    rows = []
    bmask = brk.to_numpy()
    for si, s in enumerate(cols):
        for i in np.where(bmask[:, si])[0]:
            dj, fj = i + T, i + T + H
            if fj >= len(idx) or not (C[dj, si] > 0) or not (A[i, si] > 0):
                continue
            w = slice(i, dj + 1)                       # confirmation window [break, break+T]
            cw, hw, lw, qw, tw = C[w, si], Hi[w, si], Lo[w, si], Q[w, si], TB[w, si]
            lvl_i = lv[i, si]; a = A[i, si]
            rng = hw.max() - lw.min()
            post = {
                "held": float(C[dj, si] >= lvl_i),
                "bars_above": float((cw >= lvl_i).mean()),
                "min_pullback": (lw.min() / lvl_i - 1.0),          # retest depth (neg = dipped below level)
                "close_pos": ((cw[-1] - lw.min()) / (rng + 1e-9)),  # where close sits in window range
                "range_atr": (rng / (a + 1e-9)),                    # consolidation width
                "ext_max": (hw.max() / lvl_i - 1.0),
                "vol_persist": (qw[1:].mean() / (qw[0] + 1e-9)),    # did volume hold after the break bar?
                "taker_w": (tw.sum() / (qw.sum() + 1e-9)),
                "ret_window": (cw[-1] / cw[0] - 1.0),               # post-break drift
            }
            fwd = C[fj, si] / C[dj, si] - 1.0
            rel = fwd - (btc_fwd[dj] if np.isfinite(btc_fwd[dj]) else 0.0)
            row = {"date": idx[i], "held": bool(C[dj, si] >= lvl_i), "fwd_rel": rel}
            for k in prem:
                row["pre_" + k] = prem[k][i, si]
            row.update({"post_" + k: v for k, v in post.items()})
            rows.append(row)
    tr = pd.DataFrame(rows)
    tr["win"] = (tr.fwd_rel > 0).astype(int); tr["runner"] = (tr.fwd_rel > 0.05).astype(int)
    pre_f = [c for c in tr.columns if c.startswith("pre_")]
    post_f = [c for c in tr.columns if c.startswith("post_")]
    print(f"events={len(tr)}  held={int(tr.held.sum())}  failed={int((~tr.held).sum())}  "
          f"pre={len(pre_f)} post={len(post_f)}\n")

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
        m = oof.notna()
        if m.sum() < 50 or g.loc[m, target].nunique() < 2:
            return np.nan, np.nan
        a = roc_auc_score(g.loc[m, target], oof[m])
        rng = np.random.default_rng(0); ysh = pd.Series(rng.permutation(g[target].values), index=g.index)
        return a, roc_auc_score(ysh[m], oof[m])

    for side, nm in ((True, "HELD (long)"), (False, "FAILED (short)")):
        g = tr[tr.held == side].reset_index(drop=True)
        print(f"=== {nm}: n={len(g)} win={g.win.mean():.3f} runner={g.runner.mean():.3f} ===")
        for target in ("win", "runner"):
            ap_, sp = auc(g, pre_f, target)
            aa, sa = auc(g, pre_f + post_f, target)
            print(f"  AUC[{target}]  PRE-only={ap_:.3f} (sh {sp:.3f})   PRE+POST={aa:.3f} (sh {sa:.3f})   "
                  f"post gain={aa-ap_:+.3f}")
        print()


if __name__ == "__main__":
    main()
