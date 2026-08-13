"""PRL-PEER: empirical correlation baskets + peer-residual signal (spec §8, §9, H5).

Group symbols into correlation baskets from trailing daily residual-return correlations
(distance d = sqrt(0.5*(1-rho)), agglomerative average-linkage, monthly PIT refit),
build a leave-one-out peer factor, and measure relative strength WITHIN the basket
(peer-residual = eps - peer_LOO). The question (H5): does peer-residual momentum add
incremental OOS rank-IC over market-only residual momentum?

Clusters are chosen by a fixed unsupervised K (not PnL, spec §8.4). IS only, OOS reserved.
Run: python -m anomaly_science.strategy.prl.research.peer --k 12
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.panel import KLINES_DAILY_PANEL
from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import ic as icmod
from anomaly_science.strategy.prl.research.policy import PRIMARY
from anomaly_science.strategy.prl.research.run_coarse import _warmup


def build_clusters(eps, umask, lookback=120, refit=21, k=12):
    """PIT agglomerative correlation clusters; membership frozen between refits."""
    from sklearn.cluster import AgglomerativeClustering
    dates = eps.index
    assign = pd.DataFrame(np.nan, index=dates, columns=eps.columns)
    turnover, sizes, singles = [], [], []
    prev_labels = None
    for i in range(lookback, len(dates), refit):
        win = eps.iloc[i - lookback:i]
        uni = [c for c in eps.columns if bool(umask.iloc[i].get(c, False))]
        sub = win[uni].dropna(axis=1, thresh=int(lookback * 0.6))
        if sub.shape[1] < k + 2:
            continue
        corr = sub.corr().fillna(0.0)
        dist = np.sqrt(np.clip(0.5 * (1 - corr.to_numpy()), 0, None))
        np.fill_diagonal(dist, 0.0)
        cl = AgglomerativeClustering(n_clusters=k, metric="precomputed", linkage="average")
        labels = pd.Series(cl.fit_predict(dist), index=sub.columns)
        for j in range(i, min(i + refit, len(dates))):
            assign.iloc[j] = labels.reindex(assign.columns)
        vc = labels.value_counts()
        sizes.append(vc.median()); singles.append(float((vc == 1).mean()))
        if prev_labels is not None:
            common = labels.index.intersection(prev_labels.index)
            if len(common) > 10:
                # fraction whose (cluster) co-membership set changed = rough turnover
                turnover.append(float((labels.reindex(common).values != prev_labels.reindex(common).values).mean()))
        prev_labels = labels
    diag = {"median_cluster_size": float(np.mean(sizes)) if sizes else np.nan,
            "singleton_rate": float(np.mean(singles)) if singles else np.nan,
            "label_turnover": float(np.mean(turnover)) if turnover else np.nan}
    return assign, diag


def peer_loo(eps, assign):
    """Leave-one-out peer-mean residual per (day, symbol)."""
    peer = pd.DataFrame(np.nan, index=eps.index, columns=eps.columns)
    cols = eps.columns
    for t in range(len(eps.index)):
        e = eps.iloc[t]; c = assign.iloc[t]
        d = pd.DataFrame({"e": e.values, "c": c.values}, index=cols).dropna()
        if d.empty:
            continue
        g = d.groupby("c")["e"]
        csum = g.transform("sum"); ccnt = g.transform("count")
        loo = (csum - d["e"]) / (ccnt - 1).replace(0, np.nan)
        peer.iloc[t] = loo.reindex(cols)
    return peer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=12)
    args = ap.parse_args()
    oos = pd.Timestamp(PRIMARY.oos_start, tz="UTC")
    panel = pn.load_panel(is_only=True, source_glob=KLINES_DAILY_PANEL, is_end=oos)
    p = replace(PRIMARY, universe_n=100)
    qv = pn.pivot(panel, "quote_volume")
    um = pn.build_universe_mask(panel, qv, 100, p.liquidity_lb, p.min_age_days)
    m = fx.build_coarse(panel, um, p)
    eps, close, um, label = m["eps"], m["close"], m["umask"], m["label"]

    print(f"building PIT correlation clusters (k={args.k}) ...")
    assign, diag = build_clusters(eps, um, k=args.k)
    print(f"  cluster diagnostics: median size={diag['median_cluster_size']:.1f}  "
          f"singleton rate={diag['singleton_rate']:.2f}  label turnover/refit={diag['label_turnover']:.2f}")

    peer = peer_loo(eps, assign)
    peer_resid = eps - peer                       # relative strength within the basket
    base_score = m["score"]                       # market-residual momentum (baseline)
    peer_score = fx.residual_momentum_score(peer_resid, um, p)
    comb = ((base_score.rank(axis=1, pct=True) + peer_score.rank(axis=1, pct=True)) / 2).where(um)

    warmup = _warmup(p)
    ev = eps.index[warmup:]
    print("\n=== incremental value: rank-IC vs future market-residual label ===")
    reg = pd.to_datetime(eps.index).year
    for name, sc in (("market-only (base)", base_score), ("peer-residual", peer_score),
                     ("base+peer combo", comb)):
        ic = icmod.daily_ic(sc, label, p, ev)
        s = icmod.summarize_ic(ic)
        ic.index = pd.to_datetime(ic.index)
        yr = {y: g.mean() for y, g in ic.groupby(ic.index.year)}
        ys = " ".join(f"{y}:{v:+.3f}" for y, v in yr.items())
        print(f"  {name:20s} IC={s['ic_mean']:+.4f} (t={s['t_stat']:+.1f})  by-year [{ys}]")
    print("  (peer adds value only if peer/combo IC > base by a stable, per-year margin)")


if __name__ == "__main__":
    main()
