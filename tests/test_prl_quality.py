"""PRL-QUALITY-000 feature tests: causality + bounded path descriptors."""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import quality as ql
from anomaly_science.strategy.prl.research.policy import PRLCoarsePolicy


def _panel(n_days=140, n_sym=25, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n_days, freq="D", tz="UTC")
    syms = [f"S{i:02d}" for i in range(n_sym)]
    steps = rng.normal(0, 0.03, size=(n_days, n_sym))
    close = pd.DataFrame(100 * np.exp(np.cumsum(steps, axis=0)), index=dates, columns=syms)
    return close, pd.DataFrame(True, index=dates, columns=syms)


def _pol(**kw):
    base = dict(mom_lbs=(3, 5), fwd_horizon=4, beta_lb=10, beta_min_periods=5, min_xs=5)
    base.update(kw)
    return PRLCoarsePolicy(**base)


def _eps(close, umask, p):
    ret = fx.daily_returns(close)
    rm = fx.market_factor(ret, umask, p)
    beta = fx.rolling_beta(ret, rm, p)
    return fx.daily_residual(ret, rm, beta)


def test_path_eff_and_burst_bounded_0_1():
    close, umask = _panel()
    p = _pol()
    feats = ql.quality_features(_eps(close, umask, p), umask, p, q_lb=20)
    for name in ("path_eff", "burst", "frac_pos"):
        v = feats[name].to_numpy()
        v = v[np.isfinite(v)]
        assert v.min() >= -1e-9 and v.max() <= 1 + 1e-9


def test_quality_features_are_causal():
    close, umask = _panel()
    p = _pol()
    base = ql.quality_features(_eps(close, umask, p), umask, p, q_lb=20)["path_eff"]
    shocked = close.copy()
    shocked.iloc[80:] *= 1.4  # future shock
    after = ql.quality_features(_eps(shocked, umask, p), umask, p, q_lb=20)["path_eff"]
    a, b = base.iloc[30:78], after.iloc[30:78]
    both = a.notna() & b.notna()
    assert np.allclose(a.values[both.values], b.values[both.values], atol=1e-10)


def test_composite_high_for_smooth_persistent_leader():
    # one symbol rises smoothly every day, another zig-zags to a similar net move
    dates = pd.date_range("2023-01-01", periods=60, freq="D", tz="UTC")
    smooth = np.linspace(0, 0.6, 60)
    zig = np.cumsum(([0.1, -0.08] * 30))[:60]
    close = pd.DataFrame({
        "SMOOTH": 100 * np.exp(smooth),
        "ZIG": 100 * np.exp(zig),
        "FLAT": 100 * np.exp(np.zeros(60)),
    }, index=dates)
    umask = pd.DataFrame(True, index=dates, columns=close.columns)
    p = _pol(min_xs=2)
    feats = ql.quality_features(_eps(close, umask, p), umask, p, q_lb=20)
    # smooth path efficiency should exceed the zig-zag's on the last valid row
    pe = feats["path_eff"].dropna()
    assert pe["SMOOTH"].iloc[-1] > pe["ZIG"].iloc[-1]
