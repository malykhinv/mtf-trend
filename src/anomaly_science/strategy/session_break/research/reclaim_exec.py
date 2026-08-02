"""Execution test: score every reclaim event OOF (week-grouped CatBoost MID-ranker)
and evaluate the SHORT trade (target = range mid, stop above poke high, sign-clean
gross R) by score bucket. GROSS, breakeven cost, NET at a realistic cost, win rate
and week-stability -- does the strong MID discrimination convert to positive R?

Also writes an OOF `score` back to the events parquet so the desk emitter can pick
the top-quality setups.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.model_selection import GroupKFold

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
DROP = {"symbol", "tf", "variant", "session", "break_ts", "week", "label", "trail_turnover",
        "gross_r", "risk_frac", "ref_high", "ref_low", "ref_mid", "poke_high", "entry_ts",
        "entry_px", "poke_ts", "ref_start_ts"}


def _cb():
    return CatBoostClassifier(depth=4, iterations=300, learning_rate=0.05, l2_leaf_reg=6.0,
                             loss_function="Logloss", random_seed=0, verbose=False, thread_count=6)


def score_oof(t, feats):
    X = t[feats].to_numpy(float)
    med = np.nanmedian(X, axis=0); ix = np.where(~np.isfinite(X)); X[ix] = np.take(med, ix[1])
    y = t.label.to_numpy(int); wk = t.week.to_numpy()
    oof = np.full(len(y), np.nan)
    for tr, te in GroupKFold(5).split(X, y, wk):
        m = _cb(); m.fit(X[tr], y[tr]); oof[te] = m.predict_proba(X[te])[:, 1]
    return oof


def net(t, c):
    return (t.gross_r - c / t.risk_frac).clip(-10, 10)


def line(t, c, lbl):
    if len(t) == 0:
        return f"{lbl:14s} (empty)"
    nr = net(t, c); wk = t.assign(x=nr).groupby("week")["x"].sum()
    be = t.gross_r.mean() / (1.0 / t.risk_frac).mean()
    return (f"{lbl:14s} n={len(t):6d} MID={t.label.mean():.3f} gross={t.gross_r.clip(-10,10).mean():+.3f} "
            f"net={nr.mean():+.3f} win={(nr>0).mean():.2f} pos_wk={(wk>0).mean():.2f} "
            f"be_cost={be*1e4:.0f}bps risk={100*t.risk_frac.median():.2f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, required=True)
    ap.add_argument("--cost", type=float, default=0.0006)
    ap.add_argument("--write-scores", type=Path, default=None)
    args = ap.parse_args()
    raw = pd.read_parquet(args.events)
    t = raw[(raw.break_ts < DEV_END) & (raw.label >= 0) & raw.gross_r.notna() & (raw.risk_frac > 0)].copy()
    feats = [c for c in t.columns if c not in DROP]
    t = t.reset_index(drop=True)
    t["score"] = score_oof(t, feats)
    t = t[t.score.notna()]
    print(f"{args.events.name}: n={len(t):,}  MID base={t.label.mean():.3f}  feats={len(feats)}")

    print("\n" + line(t, args.cost, "ALL"))
    for q, lbl in [(0.5, "top 50%"), (0.8, "top 20%"), (0.9, "top 10%"), (0.97, "top 3%"), (0.99, "top 1%")]:
        thr = t.score.quantile(q)
        print(line(t[t.score >= thr], args.cost, lbl))

    print("\n=== top-decile at several costs ===")
    top = t[t.score >= t.score.quantile(0.9)]
    for c in [0.0, 0.0006, 0.0012, 0.0020, 0.0030]:
        print(line(top, c, f"  {c*1e4:.0f}bps"))

    if args.write_scores:
        t.to_parquet(args.write_scores)
        print(f"\nwrote scored events -> {args.write_scores}")


if __name__ == "__main__":
    main()
