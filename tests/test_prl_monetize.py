"""Monetize math guards: neutralization orthogonality + dollar-neutral weights."""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research import monetize as mz
from anomaly_science.strategy.prl.research.policy import PRLCoarsePolicy


def _mats(n=30, k=25, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")
    cols = [f"S{i:02d}" for i in range(k)]
    umask = pd.DataFrame(True, index=idx, columns=cols)
    return idx, cols, umask, rng


def test_neutralize_removes_control_dependence():
    idx, cols, umask, rng = _mats()
    ctrl = pd.DataFrame(rng.normal(size=(len(idx), len(cols))), index=idx, columns=cols)
    cr = mz._xs_rank(ctrl, umask)
    # score strongly driven by the control RANK (how neutralize is actually used)
    score = 3 * cr + pd.DataFrame(rng.normal(0, 0.1, size=cr.shape), index=idx, columns=cols)
    neu = mz.neutralize(score, [cr], umask)

    def mean_abs_corr(a_df):
        cs = []
        for d in idx:
            a, b = a_df.loc[d], cr.loc[d]
            ok = a.notna() & b.notna()
            if ok.sum() > 5:
                cs.append(np.corrcoef(a[ok], b[ok])[0, 1])
        return abs(np.nanmean(cs))

    assert mean_abs_corr(mz._xs_rank(score, umask)) > 0.8   # before: highly dependent
    assert mean_abs_corr(neu) < 0.15                        # after: dependence removed


def test_rank_weighted_weights_are_dollar_neutral():
    idx, cols, umask, rng = _mats()
    score = pd.DataFrame(rng.normal(size=(len(idx), len(cols))), index=idx, columns=cols)
    label = pd.DataFrame(rng.normal(size=(len(idx), len(cols))), index=idx, columns=cols)
    p = PRLCoarsePolicy(min_xs=5, fwd_horizon=3)
    # a positive-mean series only if score predicts label; here independent -> ~0, finite
    r = mz.rank_weighted_factor(score, label, umask, p, warmup=3)
    assert np.isfinite(r.to_numpy()).all()
    # weights (rank-0.5) sum to ~0 -> factor return of a constant label is ~0
    const = label.copy(); const.loc[:, :] = 0.05
    r0 = mz.rank_weighted_factor(score, const, umask, p, warmup=3)
    assert abs(r0.mean()) < 1e-9
