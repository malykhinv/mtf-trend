"""Composite quality / anti-pump score (§8 follow-up).

Higher score = calmer, whale-backed, non-pump name (long candidate).
Lower score  = violent / high-vol / retail-frenzy pump name (short candidate).

Built ONLY from the IS-stable, interpretable features whose monthly rank-IC held
sign across all 6 IS months (daily_vol, realized variance/semivariance, max abs
return, avg trade size). Deliberately a small, transparent cross-sectional rank
average — not the 42-feature CatBoost model — to keep researcher degrees of freedom
low when this feeds a tradeable portfolio.

All inputs are causal (data <= day t, shifted by skip). Score is a per-date
percentile in [0,1]; NaN outside the point-in-time universe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research.policy import XSectMomentumPolicy


def _roll(mat: pd.DataFrame, win: int, fn: str) -> pd.DataFrame:
    return getattr(mat.rolling(win, min_periods=max(3, win // 2)), fn)()


def _xs_rank(mat: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """Per-date cross-sectional percentile rank, restricted to the universe."""
    m = mat.where(mask)
    return m.rank(axis=1, pct=True)


def quality_score(panel: pd.DataFrame, p: XSectMomentumPolicy, umask: pd.DataFrame) -> pd.DataFrame:
    """Composite quality score (date x symbol), higher = better long candidate."""
    close = pn.pivot(panel, "close")
    qv = pn.pivot(panel, "quote_volume")
    ntr = pn.pivot(panel, "number_of_trades")
    ret = close.pct_change(fill_method=None)
    L = p.short_lb

    daily_vol = _roll(ret, p.vol_lb, "std").shift(p.skip)
    avg_trade = (qv / ntr.replace(0, np.nan))
    avg_trade_m = _roll(avg_trade, L, "mean").shift(p.skip)

    def pm(col):
        return _roll(pn.pivot(panel, col), L, "mean").shift(p.skip) if col in panel.columns else None

    rvar = pm("intraday_realized_variance")
    maxabs = pm("intraday_max_absolute_return")
    hhi = pm("intraday_quote_volume_hhi")

    # verticality (pump signature): share of short-lb up-move in the single best day
    up = ret.clip(lower=0)
    verticality = (_roll(up, L, "max") / (_roll(up, L, "sum") + 1e-9)).shift(p.skip)

    # each component ranked so that HIGH rank = GOOD (calm, whale, non-pump)
    comps = []
    comps.append(_xs_rank(-daily_vol, umask))       # low vol good
    comps.append(_xs_rank(avg_trade_m, umask))      # whale-sized trades good
    if rvar is not None:
        comps.append(_xs_rank(-rvar, umask))        # low intraday variance good
    if maxabs is not None:
        comps.append(_xs_rank(-maxabs, umask))      # non-violent good
    if hhi is not None:
        comps.append(_xs_rank(-hhi, umask))         # unconcentrated volume good
    comps.append(_xs_rank(-verticality, umask))     # non-vertical good

    stack = pd.concat([c.stack(dropna=False) for c in comps], axis=1)
    score = stack.mean(axis=1).unstack()
    return score.reindex(index=close.index, columns=close.columns)
