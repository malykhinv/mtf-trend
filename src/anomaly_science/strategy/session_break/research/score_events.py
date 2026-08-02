"""Attach an out-of-fold ranker score to every DEV session-break event.

Within each (variant x tier) stratum, the runner ranker is trained ONLY on the
tercile labels (top vs bottom of forward-MFE within variant x tier x session),
but every event in the stratum is SCORED out-of-fold via week-grouped folds:
in each fold the model is fit on the training weeks' labelled events and scores
the held-out weeks' *entire* event set. Scores never see their own week -> no
leak into the downstream execution test.

Adds ``score`` (P(runner), NaN where unscored) and ``score_pct`` (within-stratum
percentile of score) so execution can select the top decile.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

from anomaly_science.strategy.session_break.research.portrait import (
    DEV_END_MS, FEATURES, _clean, _make_model, add_features,
)

DEFAULT_EVENTS = Path(".output/results/session_break/events.parquet")
DEFAULT_OUT = Path(".output/results/session_break/events_scored.parquet")


def _tercile_labels(ev: pd.DataFrame, target: str) -> pd.Series:
    """1 = top tercile, 0 = bottom, NaN = middle, per variant x tier x session.

    Binary first-passage targets (``fp_*``, ``rev_hit``) are already 1/0/-1: use
    the value directly, dropping the -1 (unresolved) rows.
    """
    if target.startswith("fp_") or target == "rev_hit":
        v = ev[target].astype(float)
        return v.where(v >= 0, np.nan)
    lab = pd.Series(np.nan, index=ev.index)
    for _, idx in ev.groupby(["variant", "tier", "session"]).groups.items():
        g = ev.loc[idx, target]
        if len(g) < 30:
            continue
        lo, hi = g.quantile([1 / 3, 2 / 3])
        lab.loc[idx[g >= hi]] = 1.0
        lab.loc[idx[g <= lo]] = 0.0
    return lab


def score_all(ev: pd.DataFrame, target: str = "mfe120_atr", model: str = "gbm") -> pd.DataFrame:
    ev = ev.copy()
    ev["_lab"] = _tercile_labels(ev, target)
    ev["score"] = np.nan
    for (variant, tier), idx in ev.groupby(["variant", "tier"]).groups.items():
        sub = ev.loc[idx]
        weeks = sub["week"].to_numpy()
        if len(np.unique(weeks)) < 5:
            continue
        X = _clean(sub[FEATURES])
        lab = sub["_lab"].to_numpy(float)
        gkf = GroupKFold(n_splits=5)
        # split on ALL rows; train only on labelled rows within the training weeks
        dummy_y = np.nan_to_num(lab, nan=0).astype(int)
        scores = np.full(len(sub), np.nan)
        for tr, te in gkf.split(X, dummy_y, weeks):
            tr_lab = tr[np.isfinite(lab[tr])]
            ytr = lab[tr_lab].astype(int)
            if len(np.unique(ytr)) < 2 or len(tr_lab) < 50:
                continue
            sc = StandardScaler().fit(X[tr_lab])
            clf = _make_model(model)
            clf.fit(sc.transform(X[tr_lab]), ytr)
            scores[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]
        ev.loc[idx, "score"] = scores
    # within-stratum percentile of the score
    ev["score_pct"] = (
        ev.groupby(["variant", "tier"])["score"].rank(pct=True)
    )
    return ev.drop(columns=["_lab"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--target", default="mfe120_atr")
    ap.add_argument("--model", default="gbm")
    args = ap.parse_args()

    ev = pd.read_parquet(args.events)
    ev = ev[ev["break_ts"] < DEV_END_MS]  # DEV only; OOS frozen
    ev = add_features(ev)
    scored = score_all(ev, target=args.target, model=args.model)
    n_scored = int(scored["score"].notna().sum())
    print(f"scored {n_scored:,}/{len(scored):,} DEV events")
    print(scored.groupby(["variant", "tier"])["score"].agg(["count", "mean"]))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    scored.to_parquet(args.out)
    print(f"wrote -> {args.out}")


if __name__ == "__main__":
    main()
