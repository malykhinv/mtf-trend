"""Volatility SQUEEZE -> expansion: a fresh event mechanic (not a level break).

Market-logic: when volatility compresses (Bollinger band width in a low percentile, price
coiling inside Keltner) the market stores energy; the subsequent EXPANSION often trends. We
detect a squeeze (bandwidth in its low rolling quantile) that RELEASES (bandwidth expands +
price closes outside the coil), then measure the forward move -- win-rate & runner-rate &
mean, direction-conditioned (long if released up / short if down), plus a walk-forward AUC on
whether direction/size is predictable, and per-year. If squeeze-expansion has a directional
edge where plain breakouts do not, it is a new tradeable mechanic. IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.squeeze --n 150
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research.catboost_rank import time_folds
from anomaly_science.strategy.prl.research.hourly_study import _liquid, load_1h

T, HF = 4, 24        # decision lag, forward horizon (hours)


def _roll(m, w, fn):
    return getattr(m.rolling(w, min_periods=max(2, w // 2)), fn)()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=150); args = ap.parse_args()
    print(f"loading 1h (top-{args.n}) ...")
    panel = load_1h(_liquid(args.n))
    px = lambda c: panel.pivot(index="date", columns="symbol", values=c)
    close, high, low, qv = (px(c) for c in ("close", "high", "low", "quote_volume"))
    idx, cols = close.index, close.columns
    btc = close["BTCUSDT"] if "BTCUSDT" in cols else close.median(axis=1)
    btc_fwd = (btc.shift(-(T + HF)) / btc.shift(-T) - 1.0).to_numpy()
    ma = _roll(close, 20, "mean"); sd = _roll(close, 20, "std")
    bw = (2 * sd) / ma                                  # Bollinger bandwidth
    bw_q = bw.rolling(168, min_periods=48).apply(lambda x: (x.iloc[-1] <= x).mean(), raw=False)  # low pct = squeeze
    pc = close.shift(1)
    atr = (high - low).combine((high - pc).abs(), np.maximum).combine((low - pc).abs(), np.maximum).rolling(24, min_periods=12).mean()
    upper, lowerb = ma + 2 * sd, ma - 2 * sd
    in_sq = (bw_q < 0.2)                                 # bandwidth in its lowest 20% = coiled
    rel_up = close > upper.shift(1)                      # release upward
    rel_dn = close < lowerb.shift(1)                     # release downward
    sq_prev = in_sq.shift(1).fillna(False)
    fire_up = sq_prev & rel_up
    fire_dn = sq_prev & rel_dn

    C, A = close.to_numpy(), atr.to_numpy()
    up, dn = fire_up.to_numpy(), fire_dn.to_numpy()
    ret1 = close.pct_change(fill_method=None)
    F = {"bw": bw.to_numpy(), "bwq": bw_q.to_numpy(),
         "mom24": (close / close.shift(24) - 1).to_numpy(),
         "mom72": (close / close.shift(72) - 1).to_numpy(),
         "vsurge": (qv / (_roll(qv, 168, "mean") + 1e-9)).to_numpy(),
         "dist_ma": (close / ma - 1).to_numpy(),
         "atr_now": (atr / close).to_numpy()}
    rows = []
    for si in range(len(cols)):
        for kind, arr, sgn in (("up", up, 1.0), ("dn", dn, -1.0)):
            for i in np.where(arr[:, si])[0]:
                dj, fj = i + T, i + T + HF
                if fj >= len(idx) or not (C[dj, si] > 0) or not (A[i, si] > 0):
                    continue
                raw = C[fj, si] / C[dj, si] - 1.0
                rel = (raw - (btc_fwd[dj] if np.isfinite(btc_fwd[dj]) else 0.0))
                dirret = sgn * rel                     # return in the released direction (long up / short dn)
                row = {"date": idx[i], "dir": kind, "fwd_dir": dirret}
                for k, mat in F.items():
                    row[k] = mat[i, si]
                rows.append(row)
    tr = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    tr["win"] = (tr.fwd_dir > 0).astype(int); tr["runner"] = (tr.fwd_dir > 0.05).astype(int)
    print(f"\nsqueeze-release events: {len(tr)} (up {int((tr.dir=='up').sum())}, dn {int((tr.dir=='dn').sum())})")
    print(f"  directional win-rate (move continues in release direction): {tr.win.mean():.3f}  runner {tr.runner.mean():.3f}")
    print(f"  mean fwd (dir) {tr.fwd_dir.mean()*100:+.2f}%  median {tr.fwd_dir.median()*100:+.2f}%")
    for kind, gg in tr.groupby("dir"):
        print(f"    {kind}: n={len(gg)} win={gg.win.mean():.3f} meanFwd={gg.fwd_dir.mean()*100:+.2f}% medFwd={gg.fwd_dir.median()*100:+.2f}% "
              f"by-year " + " ".join(f"{y}:{g2.fwd_dir.mean()*100:+.1f}%" for y, g2 in gg.groupby("year")))
    # blind baseline: random-direction squeeze-release
    print(f"  BLIND (release-direction is coin-flip vs actual): continuation baseline win would be ~0.50")

    feats = [c for c in tr.columns if c not in ("date", "dir", "fwd_dir", "year", "win", "runner")]
    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score
    dn_ = pd.to_datetime(tr["date"]).dt.tz_localize(None)
    def wf(tgt):
        oof = pd.Series(np.nan, index=tr.index)
        for tr_d, te_d in time_folds(dn_.values, 5, embargo=2):
            tri = tr.index[dn_.isin(tr_d)]; tei = tr.index[dn_.isin(te_d)]
            if len(tri) < 200 or tr.loc[tri, tgt].nunique() < 2:
                continue
            cb = CatBoostClassifier(iterations=250, depth=4, learning_rate=0.03, l2_leaf_reg=8, random_seed=0, verbose=False)
            cb.fit(tr.loc[tri, feats], tr.loc[tri, tgt]); oof.loc[tei] = cb.predict_proba(tr.loc[tei, feats])[:, 1]
        mm = oof.notna()
        rng = np.random.default_rng(0); ysh = pd.Series(rng.permutation(tr[tgt].values), index=tr.index)
        return roc_auc_score(tr.loc[mm, tgt], oof[mm]), roc_auc_score(ysh[mm], oof[mm])
    print("\n=== walk-forward AUC (predict continuation / runner from squeeze features) ===")
    for tgt in ("win", "runner"):
        a, s = wf(tgt); print(f"  AUC[{tgt}] = {a:.3f} (shuffle {s:.3f})")


if __name__ == "__main__":
    main()
