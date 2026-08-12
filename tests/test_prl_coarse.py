"""PRL-COARSE-000 correctness + anti-leak tests (spec §9.1, §32, §56, §71).

These lock the leak-critical invariants: the future residual label uses a beta
frozen at decision time, the signal never sees future prices, and the reserved
OOS tail is embargoed to NaN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.prl.research import factors as fx
from anomaly_science.strategy.prl.research import ic as icmod
from anomaly_science.strategy.prl.research.policy import PRLCoarsePolicy


def _panel(n_days=120, n_sym=25, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n_days, freq="D", tz="UTC")
    syms = [f"S{i:02d}" for i in range(n_sym)]
    steps = rng.normal(0, 0.03, size=(n_days, n_sym))
    close = pd.DataFrame(100 * np.exp(np.cumsum(steps, axis=0)), index=dates, columns=syms)
    umask = pd.DataFrame(True, index=dates, columns=syms)
    return close, umask


def _pol(**kw):
    base = dict(mom_lbs=(3, 5), fwd_horizon=4, beta_lb=10, beta_min_periods=5, min_xs=5)
    base.update(kw)
    return PRLCoarsePolicy(**base)


def test_future_label_frozen_beta_matches_manual():
    close, umask = _panel()
    p = _pol()
    ret = fx.daily_returns(close)
    rm = fx.market_factor(ret, umask, p)
    beta = fx.rolling_beta(ret, rm, p)
    label = fx.future_residual_label(ret, rm, beta, p)

    i, s, H = 40, "S07", p.fwd_horizon
    t = close.index[i]
    fwd_sym = ret[s].iloc[i + 1:i + 1 + H].sum()
    fwd_mkt = rm.iloc[i + 1:i + 1 + H].sum()
    expected = fwd_sym - beta.loc[t, s] * fwd_mkt
    assert label.loc[t, s] == pytest.approx(expected, rel=1e-9, abs=1e-12)


def test_signal_is_causal_future_prices_do_not_change_past_score():
    close, umask = _panel()
    p = _pol()

    def score_of(cl):
        ret = fx.daily_returns(cl)
        rm = fx.market_factor(ret, umask, p)
        beta = fx.rolling_beta(ret, rm, p)
        eps = fx.daily_residual(ret, rm, beta)
        return fx.residual_momentum_score(eps, umask, p)

    base = score_of(close)
    shocked = close.copy()
    shocked.iloc[60:] *= 1.5  # mutate the future only
    after = score_of(shocked)

    a = base.iloc[p.beta_lb:58]
    b = after.iloc[p.beta_lb:58]
    both = a.notna() & b.notna()
    assert np.allclose(a.values[both.values], b.values[both.values], atol=1e-10)


def test_label_embargoes_the_tail_to_nan():
    close, umask = _panel()
    p = _pol()
    ret = fx.daily_returns(close)
    rm = fx.market_factor(ret, umask, p)
    beta = fx.rolling_beta(ret, rm, p)
    label = fx.future_residual_label(ret, rm, beta, p)
    # last `fwd_horizon` rows have an incomplete forward window -> all NaN
    assert label.iloc[-p.fwd_horizon:].isna().all().all()


def test_trimmed_market_factor_resists_single_outlier():
    dates = pd.date_range("2023-01-01", periods=1, freq="D", tz="UTC")
    syms = [f"S{i}" for i in range(11)]
    row = [0.01] * 10 + [100.0]  # one absurd outlier
    ret = pd.DataFrame([row], index=dates, columns=syms)
    umask = pd.DataFrame(True, index=dates, columns=syms)
    trimmed = fx.market_factor(ret, umask, _pol(market_factor="trimmed_mean")).iloc[0]
    mean = fx.market_factor(ret, umask, _pol(market_factor="mean")).iloc[0]
    assert trimmed < 0.05 < mean  # trimmed ~0.01, mean ~9


def test_rolling_beta_within_shrink_clip_range():
    close, umask = _panel(seed=3)
    p = _pol()
    beta = fx.rolling_beta(fx.daily_returns(close), fx.market_factor(fx.daily_returns(close), umask, p), p)
    lo = p.beta_shrink + (1 - p.beta_shrink) * p.beta_clip_lo
    hi = p.beta_shrink + (1 - p.beta_shrink) * p.beta_clip_hi
    vals = beta.to_numpy()
    vals = vals[np.isfinite(vals)]
    assert vals.min() >= lo - 1e-9 and vals.max() <= hi + 1e-9


def test_daily_ic_perfect_when_label_shares_ranking():
    close, umask = _panel(n_days=60)
    p = _pol()
    rng = np.random.default_rng(1)
    score = pd.DataFrame(rng.normal(size=close.shape), index=close.index, columns=close.columns)
    label = score * 2.0 + 1.0  # identical per-date ranking
    ic = icmod.daily_ic(score, label, p, score.index)
    assert ic.mean() == pytest.approx(1.0, abs=1e-9)


def test_placebo_collapses_a_real_ic():
    close, umask = _panel(n_days=300, n_sym=40)
    p = _pol()
    rng = np.random.default_rng(2)
    score = pd.DataFrame(rng.normal(size=close.shape), index=close.index, columns=close.columns)
    label = score + rng.normal(0, 0.3, size=close.shape)  # strongly informative
    real = icmod.daily_ic(score, label, p, score.index).mean()
    plac = icmod.placebo_ic(score, label, p, score.index)["placebo_ic_mean"]
    assert real > 0.5
    assert abs(plac) < 0.1


def test_block_bootstrap_ci_brackets_mean():
    idx = pd.date_range("2023-01-01", periods=400, freq="D", tz="UTC")
    ic = pd.Series(np.random.default_rng(0).normal(0.03, 0.1, size=400), index=idx)
    out = icmod.block_bootstrap_mean(ic, block=20, n_boot=500, seed=0)
    assert out["boot_q05"] < out["boot_mean"] < out["boot_q95"]
