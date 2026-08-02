"""What unites WINNING reclaim trades vs LOSING ones? Target = the actual trade R
sign (gross_r>0), not the MID label. CatBoost week-OOF AUC (+ shuffled null), mean
|SHAP|, and for each feature the median among wins vs losses with the signed
Spearman -- a direct portrait of winner vs loser.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
DROP = {"symbol", "tf", "variant", "session", "break_ts", "week", "label", "trail_turnover",
        "gross_r", "risk_frac", "ref_high", "ref_low", "ref_mid", "poke_high",
        "entry_ts", "entry_px", "poke_ts", "ref_start_ts", "score", "utc_day", "win"}


def _cb():
    return CatBoostClassifier(depth=4, iterations=300, learning_rate=0.05, l2_leaf_reg=6.0,
                             loss_function="Logloss", random_seed=0, verbose=False, thread_count=6)


def _clean(X):
    med = np.nanmedian(X, axis=0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    return X


def oof(X, y, wk):
    o = np.full(len(y), np.nan)
    for tr, te in GroupKFold(5).split(X, y, wk):
        if len(np.unique(y[tr])) < 2:
            continue
        m = _cb(); m.fit(X[tr], y[tr]); o[te] = m.predict_proba(X[te])[:, 1]
    return o


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=Path(".output/results/session_break/rlabel_tf60_scored.parquet"))
    ap.add_argument("--max-n", type=int, default=120000)
    args = ap.parse_args()
    t = pd.read_parquet(args.events)
    t = t[(t.break_ts < DEV_END) & t.gross_r.notna()].copy()
    t["win"] = (t.gross_r > 0).astype(int)
    if len(t) > args.max_n:
        t = t.sample(args.max_n, random_state=0)
    t = t.reset_index(drop=True)
    feats = [c for c in t.columns if c not in DROP]
    X = _clean(t[feats].to_numpy(float)); y = t.win.to_numpy(int); wk = t.week.to_numpy()
    print(f"trades={len(t):,}  win-rate={y.mean():.3f}  feats={len(feats)}  weeks={t.week.nunique()}")

    o = oof(X, y, wk); m = np.isfinite(o)
    auc = roc_auc_score(y[m], o[m])
    rng = np.random.default_rng(0); yp = y.copy()
    for w in np.unique(wk):
        mm = wk == w; yp[mm] = rng.permutation(yp[mm])
    an = roc_auc_score(yp[m], oof(X, yp, wk)[m])
    print(f"win-vs-loss week-CV AUC={auc:.3f}  shuffled-null={an:.3f}  "
          f"-> {'SIGNAL' if auc>an+0.02 and auc>0.55 else 'weak'}")

    full = _cb(); full.fit(X, y)
    shap = np.abs(full.get_feature_importance(Pool(X, y), type="ShapValues")[:, :-1]).mean(0)
    rows = []
    Xc = pd.DataFrame(X, columns=feats)
    for i, f in enumerate(feats):
        rows.append({"feature": f, "shap": shap[i],
                     "win_med": Xc.loc[y == 1, f].median(), "loss_med": Xc.loc[y == 0, f].median(),
                     "spearman_win": spearmanr(X[:, i], y).correlation})
    r = pd.DataFrame(rows).sort_values("shap", ascending=False)
    print("\n=== winner vs loser portrait (top by SHAP) ===")
    print(r.head(16).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))


if __name__ == "__main__":
    main()
