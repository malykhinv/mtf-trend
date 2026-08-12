"""PRL-QUALITY-000 — persistence / path-quality of residual leadership (§14, §15).

COARSE-000 found flat residual-momentum rank has ~0 IC, yet the top decile's MEAN
was inflated by a few explosive names. H2 asks whether a *quality* axis separates
persistent, smoothly-built leaders (which may continue) from one-shot bursts (which
may reverse) — i.e. whether the fat-tail winners are the low-quality burst names.

All features are built from the causal daily residual `eps` (data <= t, shift skip);
they reuse the exact frozen-beta future residual label from `factors.py`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research.policy import PRLCoarsePolicy

QUALITY_LB = 28   # trailing window for persistence/path features (trading days)
_TINY = 1e-9


def _roll(mat: pd.DataFrame, win: int, fn: str) -> pd.DataFrame:
    return getattr(mat.rolling(win, min_periods=max(3, win // 2)), fn)()


def quality_features(eps: pd.DataFrame, umask: pd.DataFrame, p: PRLCoarsePolicy,
                     q_lb: int = QUALITY_LB) -> dict[str, pd.DataFrame]:
    """Causal residual path-quality descriptors (date x symbol, shifted by skip).

    path_eff   : |net residual| / total residual travel over q_lb  (1=smooth trend)
    frac_pos   : share of days with positive residual              (persistence)
    burst      : largest single-day |residual| / total travel      (one-shot signature)
    recent_shr : share of net residual delivered in the last q_lb/4 days (acceleration)
    """
    travel = _roll(eps.abs(), q_lb, "sum")
    net = _roll(eps, q_lb, "sum")
    recent = _roll(eps, max(2, q_lb // 4), "sum")
    feats = {
        "path_eff": (net.abs() / (travel + _TINY)).shift(p.skip),
        "frac_pos": _roll((eps > 0).astype(float), q_lb, "mean").shift(p.skip),
        "burst": (_roll(eps.abs(), q_lb, "max") / (travel + _TINY)).shift(p.skip),
        "recent_shr": (recent / (net.abs() + _TINY)).shift(p.skip),
    }
    return {k: v.where(umask) for k, v in feats.items()}


def quality_composite(feats: dict[str, pd.DataFrame], umask: pd.DataFrame) -> pd.DataFrame:
    """High = persistent, smooth, non-burst leader. Per-date percentile average of
    rank(path_eff) + rank(frac_pos) + rank(-burst)."""
    def r(m):
        return m.where(umask).rank(axis=1, pct=True)
    comps = [r(feats["path_eff"]), r(feats["frac_pos"]), r(-feats["burst"])]
    stack = pd.concat([c.stack(dropna=False) for c in comps], axis=1).mean(axis=1)
    return stack.unstack().reindex(index=umask.index, columns=umask.columns).where(umask)


def conditional_double_sort(mom: pd.DataFrame, qual: pd.DataFrame, label: pd.DataFrame,
                            p: PRLCoarsePolicy, warmup: int,
                            mom_top_frac: float = 1 / 3) -> dict:
    """Within the momentum leaders, does high quality beat low quality? (H2)

    Each rebalance: take the top `mom_top_frac` by residual momentum (the 'leaders'),
    split them at their median quality, and compare the MEDIAN future residual of the
    high- vs low-quality halves. Median (not mean) so a fat-tail name cannot carry it.
    """
    dates = list(mom.index[warmup::p.rebalance_days])
    spreads, hi_meds, lo_meds = [], [], []
    for d in dates:
        s = mom.loc[d].dropna()
        if len(s) < max(p.min_xs, 15):
            continue
        k = max(5, int(len(s) * mom_top_frac))
        leaders = s.sort_values(ascending=False).index[:k]
        q = qual.loc[d].reindex(leaders).dropna()
        l = label.loc[d].reindex(q.index).dropna()
        q = q.reindex(l.index)
        if len(l) < 8:
            continue
        med = q.median()
        hi = l[q >= med]; lo = l[q < med]
        if len(hi) < 3 or len(lo) < 3:
            continue
        spreads.append(hi.median() - lo.median())
        hi_meds.append(hi.median()); lo_meds.append(lo.median())
    spreads = np.array(spreads, dtype=float)
    n = len(spreads)
    side = (p.fee_bps + p.half_spread_bps + p.base_slippage_bps) * p.cost_multiplier / 1e4
    roundtrip = 4.0 * side
    gross = float(np.nanmean(spreads)) if n else np.nan
    t = (float(np.nanmean(spreads) / (np.nanstd(spreads) / np.sqrt(n)))
         if n > 2 and np.nanstd(spreads) > 0 else np.nan)
    return {
        "n_reb": n,
        "hiqual_minus_loqual_median": gross,
        "net": float(gross - roundtrip) if n else np.nan,
        "t": t,
        "pos_reb_share": float(np.nanmean(spreads > 0)) if n else np.nan,
        "hi_med": float(np.nanmean(hi_meds)) if n else np.nan,
        "lo_med": float(np.nanmean(lo_meds)) if n else np.nan,
    }
