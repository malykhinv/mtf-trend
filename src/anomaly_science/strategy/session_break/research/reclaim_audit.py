"""Look-ahead / optimistic-bias audit for the MID-vs-ABOVE discrimination.

Three CV schemes on the SAME features/model, plus selection accounting:
  1. week-GroupKFold  -- the headline number (test week can sit BETWEEN train weeks
     = mild interpolation optimism);
  2. forward-temporal -- train on the earliest ~75% of weeks, test on the latest
     ~25% (pure extrapolation; the honest deployment analogue);
  3. symbol-GroupKFold -- train on some symbols, test on entirely unseen symbols
     (guards against the model memorising per-coin quirks).
  4. label-shuffled null under scheme 1 (structural-leak tripwire).

Also reports how many events were unresolved (neither MID nor ABOVE) and dropped,
and how many DEV events had an outcome horizon crossing into OOS (boundary effect).
If AUC survives 2 and 3 near the headline, there is no material look-ahead or
optimistic bias.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

DEV_END = pd.Timestamp("2026-01-01", tz="UTC").value // 10**6
# exclude ids, the label, AND the trade/geometry/outcome columns added post-audit
# (gross_r/risk_frac are OUTCOMES; ref_*/poke_*/entry_* are raw prices) so the audit
# uses ONLY the 29 causal features that actually feed the desk score.
DROP = {"symbol", "tf", "variant", "session", "break_ts", "week", "label", "trail_turnover",
        "gross_r", "risk_frac", "ref_high", "ref_low", "ref_mid", "poke_high",
        "entry_ts", "entry_px", "poke_ts", "ref_start_ts", "score", "utc_day"}


def _cb():
    return CatBoostClassifier(depth=4, iterations=250, learning_rate=0.05, l2_leaf_reg=6.0,
                             loss_function="Logloss", random_seed=0, verbose=False, thread_count=6)


def _clean(X):
    med = np.nanmedian(X, axis=0); ix = np.where(~np.isfinite(X))
    X[ix] = np.take(med, ix[1])
    return X


def grouped_auc(X, y, groups):
    o = np.full(len(y), np.nan)
    for tr, te in GroupKFold(5).split(X, y, groups):
        if len(np.unique(y[tr])) < 2:
            continue
        m = _cb(); m.fit(X[tr], y[tr]); o[te] = m.predict_proba(X[te])[:, 1]
    ok = np.isfinite(o)
    return roc_auc_score(y[ok], o[ok])


def forward_auc(X, y, week_ord, frac=0.75):
    cut = np.quantile(week_ord, frac)
    tr = week_ord <= cut; te = week_ord > cut
    if len(np.unique(y[tr])) < 2 or te.sum() < 100:
        return np.nan, tr.sum(), te.sum()
    m = _cb(); m.fit(X[tr], y[tr])
    p = m.predict_proba(X[te])[:, 1]
    return roc_auc_score(y[te], p), int(tr.sum()), int(te.sum())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, required=True)
    ap.add_argument("--max-n", type=int, default=90000)
    args = ap.parse_args()
    raw = pd.read_parquet(args.events)
    dev = raw[raw.break_ts < DEV_END]
    resolved = dev[dev.label >= 0]
    unresolved = int((dev.label < 0).sum())
    print(f"{args.events.name}: DEV rows(with unresolved stored as -1)?  "
          f"resolved={len(resolved):,}  unresolved(dropped)={unresolved:,} "
          f"({unresolved/max(1,len(dev)):.1%})")
    # boundary: outcome horizon may cross into OOS for events near DEV end
    near = int((resolved.break_ts > DEV_END - 2 * 24 * 3600_000).sum())
    print(f"  events within 2 days of DEV end (outcome may touch OOS): {near:,} "
          f"({near/len(resolved):.1%})")

    t = resolved.sample(min(args.max_n, len(resolved)), random_state=0).reset_index(drop=True)
    feats = [c for c in t.columns if c not in DROP]
    X = _clean(t[feats].to_numpy(float)); y = t.label.to_numpy(int)
    wk = t.week.to_numpy(); sym = t.symbol.to_numpy()
    week_ord = pd.factorize(np.sort(t.week.unique()))[0]
    wmap = {w: i for i, w in enumerate(np.sort(t.week.unique()))}
    word = np.array([wmap[w] for w in wk])

    print(f"\n  n={len(t):,}  MID base={y.mean():.3f}  feats={len(feats)}  "
          f"weeks={len(np.unique(wk))}  symbols={len(np.unique(sym))}")
    a_week = grouped_auc(X, y, wk)
    print(f"  [1] week-GroupKFold AUC     = {a_week:.3f}")
    a_fwd, ntr, nte = forward_auc(X, y, word, 0.75)
    print(f"  [2] forward-temporal AUC    = {a_fwd:.3f}   (train {ntr:,} early / test {nte:,} late)")
    a_sym = grouped_auc(X, y, sym)
    print(f"  [3] symbol-GroupKFold AUC   = {a_sym:.3f}   (unseen coins)")
    # forward top-decile MID rate: the LIVE analogue -- train strictly-past, score
    # late, and check the selected-decile precision holds out-of-time.
    cut = np.quantile(word, 0.75); tr = word <= cut; te = word > cut
    m = _cb(); m.fit(X[tr], y[tr]); pte = m.predict_proba(X[te])[:, 1]
    thr = np.quantile(pte, 0.9); top = pte >= thr
    print(f"      forward top-decile MID rate = {y[te][top].mean():.3f} "
          f"vs late-base {y[te].mean():.3f}  (n_top={int(top.sum())})")
    # shuffled null under week grouping
    rng = np.random.default_rng(0); yp = y.copy()
    for w in np.unique(wk):
        mm = wk == w; yp[mm] = rng.permutation(yp[mm])
    a_null = grouped_auc(X, yp, wk)
    print(f"  [4] label-shuffled null     = {a_null:.3f}   (should be ~0.50)")

    print("\n  verdict:", "ROBUST (no material look-ahead / optimism)"
          if (a_fwd > 0.63 and a_sym > 0.63 and a_null < 0.54) else
          "CHECK -- one scheme dropped materially")


if __name__ == "__main__":
    main()
