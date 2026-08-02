"""Is the top-decile lift a steady edge or a one-week lottery?

Prior work (runner-ness) had a real ranker whose *profit* was a single explosive
week. Before believing the session-break top-decile lift, we score every DEV break
out-of-fold (week-grouped GBM), take the top-decile-scored breaks, and check how the
realised forward MFE is distributed ACROSS weeks: what fraction of weeks are
positive vs the stratum median, and how concentrated the total lift is.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

from anomaly_science.strategy.session_break.research.portrait import (
    DEV_END_MS, FEATURES, _clean, _make_model, _runner_fizzle, add_features,
)

DEFAULT_EVENTS = Path(".output/results/session_break/events.parquet")


def score_stratum(df: pd.DataFrame, kind: str, target: str, seed: int, max_n: int):
    lab = df.groupby("session", group_keys=False).apply(lambda g: _runner_fizzle(g, target))
    if len(lab) < 300:
        return None
    if len(lab) > max_n:
        lab = lab.sample(max_n, random_state=seed).reset_index(drop=True)
    else:
        lab = lab.reset_index(drop=True)
    y = lab["runner"].to_numpy(int)
    weeks = lab["week"].to_numpy()
    X = _clean(lab[FEATURES])
    if len(np.unique(weeks)) < 5 or y.sum() < 20 or (1 - y).sum() < 20:
        return None
    gkf = GroupKFold(n_splits=5)
    oof = np.full(len(y), np.nan)
    for tr, te in gkf.split(X, y, weeks):
        if y[tr].sum() == 0 or (1 - y[tr]).sum() == 0:
            continue
        sc = StandardScaler().fit(X[tr])
        clf = _make_model(kind)
        clf.fit(sc.transform(X[tr]), y[tr])
        oof[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]
    lab = lab.assign(score=oof)
    return lab.dropna(subset=["score"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    ap.add_argument("--target", default="mfe120_atr")
    ap.add_argument("--outcome", default="mfe120_atr")
    ap.add_argument("--model", default="gbm")
    ap.add_argument("--max-n", type=int, default=40000)
    args = ap.parse_args()

    ev = pd.read_parquet(args.events)
    ev = ev[ev["break_ts"] < DEV_END_MS]
    ev = add_features(ev)

    for variant in ["sequential", "same_type"]:
        for tier in ["high", "mid", "low"]:
            sub = ev[(ev["variant"] == variant) & (ev["tier"] == tier)]
            scored = score_stratum(sub, args.model, args.target, seed=0, max_n=args.max_n)
            if scored is None:
                continue
            thr = np.nanpercentile(scored["score"], 90)
            top = scored[scored["score"] >= thr]
            base_med = scored[args.outcome].median()
            # per-week: median outcome of top-decile picks vs the week's overall median
            rows = []
            for wk, g in scored.groupby("week"):
                gt = g[g["score"] >= thr]
                if len(gt) < 3:
                    continue
                rows.append((wk, len(gt), gt[args.outcome].median(), g[args.outcome].median()))
            wk_df = pd.DataFrame(rows, columns=["week", "n_top", "top_med", "week_med"])
            if wk_df.empty:
                continue
            wk_df["beat"] = wk_df["top_med"] > wk_df["week_med"]
            frac_beat = wk_df["beat"].mean()
            # concentration: share of total top-decile MFE from the single best week
            tot = top[args.outcome].clip(lower=0).sum()
            by_week_sum = top.assign(pos=top[args.outcome].clip(lower=0)).groupby("week")["pos"].sum()
            top_week_share = by_week_sum.max() / tot if tot > 0 else np.nan
            print(f"{variant:10s} {tier:4s}  n={len(scored):6d}  top={len(top):5d}  "
                  f"top_med={top[args.outcome].median():.2f} vs base {base_med:.2f} "
                  f"(x{top[args.outcome].median()/base_med:.2f})  "
                  f"weeks_beating={frac_beat:.0%} ({len(wk_df)}w)  "
                  f"max1wk_share={top_week_share:.0%}")


if __name__ == "__main__":
    main()
