"""Where does session-streak continuation actually become predictable? Slice the
pooled events by session type, cap tier, market regime, streak length, and per
symbol -- and measure, WITHIN each slice, the discrimination (week-OOF AUC), the
top-decile realised hold return, weekly stability, against a within-week shuffled
null. Find the non-noisy pockets instead of trusting the pooled average.

Efficient design: train ONE week-OOF model per direction on all features, get an
OOF probability for every event, then slice arbitrarily and evaluate the same probs
within each slice. Also train per-session specialised models, and test whether
continuation is SYMBOL-persistent (split-half P(cont) correlation).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from anomaly_science.strategy.session_break.research.session_streak import FEATS
from anomaly_science.strategy.session_break.research.sessions import BLOCK_BY_SEQ

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
COST_BP = 8.0


def oof_probs(d: pd.DataFrame, seed: int = 0, shuffle_null: bool = False) -> np.ndarray:
    X = d[FEATS].to_numpy(float); y = d.cont.to_numpy(int); wk = d.week.to_numpy()
    if shuffle_null:  # break the label within week -> noise floor
        y = d.groupby("week").cont.transform(lambda s: np.random.permutation(s.values)).to_numpy(int)
    med = np.nanmedian(X, 0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    o = np.full(len(y), np.nan)
    for tr, te in GroupKFold(5).split(X, y, wk):
        if len(np.unique(y[tr])) < 2:
            continue
        m = CatBoostClassifier(depth=5, iterations=350, learning_rate=0.03, l2_leaf_reg=6,
                               verbose=False, random_seed=seed)
        m.fit(X[tr], y[tr]); o[te] = m.predict_proba(X[te])[:, 1]
    return o


def cell_stats(s: pd.DataFrame) -> tuple:
    """(n, base_cont, AUC, topDec_net_bp, topDec_P, pos_wk) for a slice with column p."""
    s = s[np.isfinite(s.p)]
    if len(s) < 150 or s.cont.nunique() < 2:
        return len(s), s.cont.mean() if len(s) else np.nan, np.nan, np.nan, np.nan, np.nan
    auc = roc_auc_score(s.cont, s.p)
    thr = np.nanpercentile(s.p, 90)
    td = s[s.p >= thr]
    net = td.trade_ret.mean() * 1e4 - COST_BP
    posw = (td.groupby("week").trade_ret.mean() > 0).mean()
    return len(s), s.cont.mean(), auc, net, td.cont.mean(), posw


def report(title: str, d: pd.DataFrame, by):
    print(f"\n### {title}")
    print(f"{'cell':<22}{'n':>8}{'base':>7}{'AUC':>7}{'topDec_net':>11}{'topDec_P':>9}{'pos_wk':>7}")
    rows = []
    for key, g in d.groupby(by):
        n, base, auc, net, tdp, posw = cell_stats(g)
        rows.append((key, n, base, auc, net, tdp, posw))
    for key, n, base, auc, net, tdp, posw in sorted(rows, key=lambda r: (-(r[3] if np.isfinite(r[3]) else 0))):
        k = key if isinstance(key, str) else str(key)
        a = f"{auc:.3f}" if np.isfinite(auc) else "  -  "
        nt = f"{net:+.1f}b" if np.isfinite(net) else "   -  "
        tp = f"{tdp:.3f}" if np.isfinite(tdp) else "  -  "
        pw = f"{posw:.2f}" if np.isfinite(posw) else "  - "
        print(f"{k:<22}{n:>8}{base:>7.3f}{a:>7}{nt:>11}{tp:>9}{pw:>7}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/streak.parquet"))
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    t = t[t.start_ts < DEV_END].copy()
    t["session"] = t.seq.map(lambda s: BLOCK_BY_SEQ[int(s)].name)
    t["cap_tier"] = pd.qcut(t.cap, 3, labels=["low", "mid", "high"])
    t["k_bucket"] = pd.cut(t.streak_len, [1, 3, 5, 99], labels=["2-3", "4-5", "6+"])
    t["btc_regime"] = np.where(t.get("mkt_btc_ema_dist", 0) > 0, "btc_bull", "btc_bear")
    t["alt_regime"] = np.where(t.get("mkt_alt_ema_dist", 0) > 0, "alt_bull", "alt_bear")

    for direction, dname in ((-1, "SHORT"), (+1, "LONG")):
        d = t[t.direction == direction].copy()
        d["p"] = oof_probs(d)
        # null floor (pooled)
        pnull = oof_probs(d, shuffle_null=True)
        mk = np.isfinite(d.p) & np.isfinite(pnull)
        auc_real = roc_auc_score(d.cont[mk], d.p[mk]); auc_null = roc_auc_score(d.cont[mk], pnull[mk])
        print(f"\n================ {dname}  n={len(d):,}  pooled AUC={auc_real:.3f}  (shuffled null={auc_null:.3f}) ================")
        report(f"{dname} by session", d, "session")
        report(f"{dname} by cap tier", d, "cap_tier")
        report(f"{dname} by streak length", d, "k_bucket")
        report(f"{dname} by BTC regime", d, "btc_regime")
        report(f"{dname} by alt regime", d, "alt_regime")
        report(f"{dname} by session x cap", d, ["session", "cap_tier"])
        report(f"{dname} by session x alt-regime", d, ["session", "alt_regime"])

        # SYMBOL persistence: is continuation a stable property of a coin?
        med_wk = d.week.median() if False else None
        d = d.sort_values("start_ts")
        half = d.start_ts.median()
        h1 = d[d.start_ts <= half].groupby("symbol").agg(c1=("cont", "mean"), n1=("cont", "size"))
        h2 = d[d.start_ts > half].groupby("symbol").agg(c2=("cont", "mean"), n2=("cont", "size"))
        j = h1.join(h2, how="inner")
        j = j[(j.n1 >= 20) & (j.n2 >= 20)]
        if len(j) > 30:
            r = np.corrcoef(j.c1, j.c2)[0, 1]
            print(f"\n  {dname} SYMBOL persistence: split-half P(cont) corr={r:+.3f} over {len(j)} symbols "
                  f"(>0 => continuation is a stable coin trait)")
            top = j.assign(cavg=(j.c1 * j.n1 + j.c2 * j.n2) / (j.n1 + j.n2)).sort_values("cavg", ascending=False).head(8)
            print("   most persistent continuers:", ", ".join(f"{s}={row.cavg:.2f}" for s, row in top.iterrows()))


if __name__ == "__main__":
    main()
