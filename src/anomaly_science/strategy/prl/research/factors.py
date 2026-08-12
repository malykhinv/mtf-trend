"""Market factor, as-of-t beta, residual returns and the frozen-beta future label.

Leak discipline (§5.1, §9.1, §56):
  * every SIGNAL quantity at decision-time t uses only returns on rows <= t;
  * the future residual LABEL uses future factor returns but a beta FROZEN at t;
  * forward sums use `min_periods == horizon`, so rows whose window runs past the
    loaded (IS) tail become NaN and drop out — this is the §6.3.2 embargo that
    keeps the reserved OOS tail unread.

Returns are simple daily returns; horizon and residual objects are *additive*
sums of daily returns, which keeps the beta residualization linear.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.prl.research.policy import PRLCoarsePolicy


def daily_returns(close: pd.DataFrame) -> pd.DataFrame:
    """Simple close-to-close daily return; row t known at close(t)."""
    return close.pct_change(fill_method=None)


# --------------------------------------------------------------------------- #
# Market factor (§7)
# --------------------------------------------------------------------------- #
def _trimmed_mean(row: np.ndarray, frac: float) -> float:
    v = row[np.isfinite(row)]
    if v.size < 3:
        return float(np.nanmean(v)) if v.size else np.nan
    v = np.sort(v)
    k = int(np.floor(v.size * frac))
    core = v[k: v.size - k] if v.size - 2 * k >= 1 else v
    return float(np.mean(core))


def market_factor(ret: pd.DataFrame, umask: pd.DataFrame, p: PRLCoarsePolicy) -> pd.Series:
    """Per-date robust aggregate return of the eligible universe (§7).

    trimmed_mean (default) and median make a single symbol negligible, achieving
    the leave-one-out objective (§7.1). mean_loo returns the exact common mean
    here (per-symbol LOO is applied downstream only where it matters).
    """
    masked = ret.where(umask)
    if p.market_factor == "median":
        return masked.median(axis=1)
    if p.market_factor in ("mean", "mean_loo"):
        return masked.mean(axis=1)
    if p.market_factor == "trimmed_mean":
        arr = masked.to_numpy()
        vals = np.array([_trimmed_mean(arr[i], p.trim_frac) for i in range(arr.shape[0])])
        return pd.Series(vals, index=masked.index)
    raise ValueError(f"unknown market_factor {p.market_factor!r}")


# --------------------------------------------------------------------------- #
# Rolling beta, as-of-t, shrunk (§7.2, §9.4)
# --------------------------------------------------------------------------- #
def rolling_beta(ret: pd.DataFrame, rm: pd.Series, p: PRLCoarsePolicy) -> pd.DataFrame:
    """beta_i,t = shrink*1 + (1-shrink)*clip(cov/var), trailing window ending at t.

    Uses only rows <= t (causal). Shrinkage to 1 damps the estimation noise that
    otherwise attenuates the measured IC (§9.4).
    """
    w, mp = p.beta_lb, p.beta_min_periods
    rm = rm.reindex(ret.index)
    ex = ret.rolling(w, min_periods=mp).mean()
    em = rm.rolling(w, min_periods=mp).mean()
    exm = ret.mul(rm, axis=0).rolling(w, min_periods=mp).mean()
    em2 = (rm * rm).rolling(w, min_periods=mp).mean()
    cov = exm.sub(ex.mul(em, axis=0))
    var = (em2 - em * em)
    beta_raw = cov.div(var, axis=0).clip(p.beta_clip_lo, p.beta_clip_hi)
    return p.beta_shrink * 1.0 + (1.0 - p.beta_shrink) * beta_raw


def daily_residual(ret: pd.DataFrame, rm: pd.Series, beta: pd.DataFrame) -> pd.DataFrame:
    """eps_i,t = r_i,t - beta_i,t * R_market(t); contemporaneous, causal."""
    rm = rm.reindex(ret.index)
    return ret.sub(beta.mul(rm, axis=0))


# --------------------------------------------------------------------------- #
# Signal: multi-horizon residual momentum rank (§12, §13)
# --------------------------------------------------------------------------- #
def residual_momentum_score(eps: pd.DataFrame, umask: pd.DataFrame, p: PRLCoarsePolicy) -> pd.DataFrame:
    """Composite per-date percentile of cumulative residual momentum.

    For each lookback H: sum residuals over the trailing H days, shift by skip,
    percentile-rank within the universe; average the H percentiles. Higher = a
    stronger, multi-horizon-agreeing residual leader. NaN outside the universe.
    """
    parts = []
    for H in p.mom_lbs:
        cum = eps.rolling(H, min_periods=max(3, H // 2)).sum().shift(p.skip)
        parts.append(cum.where(umask).rank(axis=1, pct=True))
    score = pd.concat([x.stack(future_stack=True) for x in parts], axis=1).mean(axis=1).unstack()
    return score.reindex(index=eps.index, columns=eps.columns).where(umask)


# --------------------------------------------------------------------------- #
# Future residual label — frozen beta (§9.1, §29)
# --------------------------------------------------------------------------- #
def future_residual_label(ret: pd.DataFrame, rm: pd.Series, beta: pd.DataFrame,
                          p: PRLCoarsePolicy) -> pd.DataFrame:
    """future_resid_i(t) = sum_{t+1..t+H} r_i - beta_i,t * sum_{t+1..t+H} R_market.

    beta is FROZEN at decision-time t (§9.1). min_periods == H means a row whose
    forward window is incomplete (the IS tail) becomes NaN and drops out (§6.3.2
    embargo), so the reserved OOS returns are never consumed.
    """
    H = p.fwd_horizon
    rm = rm.reindex(ret.index)
    fwd_sym = ret.rolling(H, min_periods=H).sum().shift(-H)
    fwd_mkt = rm.rolling(H, min_periods=H).sum().shift(-H)
    return fwd_sym.sub(beta.mul(fwd_mkt, axis=0))


# --------------------------------------------------------------------------- #
# Convenience: build the full coarse feature/label set for a loaded panel
# --------------------------------------------------------------------------- #
def build_coarse(panel: pd.DataFrame, umask: pd.DataFrame, p: PRLCoarsePolicy) -> dict:
    """Return dict of matrices: close, ret, rm, beta, eps, score, label."""
    close = pn.pivot(panel, "close")
    umask = umask.reindex(index=close.index, columns=close.columns).fillna(False)
    ret = daily_returns(close)
    rm = market_factor(ret, umask, p)
    beta = rolling_beta(ret, rm, p)
    eps = daily_residual(ret, rm, beta)
    score = residual_momentum_score(eps, umask, p)
    label = future_residual_label(ret, rm, beta, p)
    return {"close": close, "ret": ret, "rm": rm, "beta": beta,
            "eps": eps, "score": score, "label": label, "umask": umask}
