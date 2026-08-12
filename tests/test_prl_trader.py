"""Trader-summary math guards (concentration flip, portfolio, equity metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.prl.research import trader_summary as ts


def test_top_trades_to_flip_small_when_one_winner_carries():
    # 9 losers of -0.01 (sum -0.09) + 1 winner of +0.20 -> total +0.11.
    # removing the single top trade drops total to -0.09 <= 0 -> 1/10 = 0.1
    tr = pd.DataFrame({"net": [-0.01] * 9 + [0.20]})
    assert ts._top_trades_to_flip(tr) == 0.1


def test_top_trades_to_flip_zero_when_already_negative():
    tr = pd.DataFrame({"net": [-0.01, -0.02, 0.005]})
    assert ts._top_trades_to_flip(tr) == 0.0


def test_portfolio_returns_caps_gross_at_one():
    # 40 names at 3% each would be 120% gross -> capped to equal-weight 1/n
    tr = pd.DataFrame({"rebalance_date": ["d"] * 40, "net": [0.01] * 40})
    r = ts._portfolio_returns(tr, risk=0.03)
    assert abs(r.iloc[0] - 0.01) < 1e-9  # mean net at full (capped) gross


def test_equity_metrics_basic():
    ret = pd.Series([0.01, -0.005, 0.02, 0.0, 0.01])
    m = ts._equity_metrics(ret, hold=5)
    assert m["final_equity"] > 1.0
    assert m["max_dd"] <= 0.0
    assert np.isfinite(m["sharpe"])
