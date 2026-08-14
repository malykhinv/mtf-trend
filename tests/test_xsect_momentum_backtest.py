from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.xsect_momentum.research import backtest as bt
from anomaly_science.strategy.xsect_momentum.research.gates import evaluate_gates
from anomaly_science.strategy.xsect_momentum.research.policy import XSectMomentumPolicy


def _fixture(prices: np.ndarray) -> tuple[pd.DataFrame, ...]:
    dates = pd.date_range("2025-01-01", periods=len(prices), tz="UTC")
    close = pd.DataFrame({"AUSDT": prices}, index=dates)
    open_ = close.copy()
    quote_volume = pd.DataFrame({"AUSDT": 1_000_000.0}, index=dates)
    universe = pd.DataFrame({"AUSDT": True}, index=dates)
    scores = pd.DataFrame({"AUSDT": 1.0}, index=dates)
    panel = pd.DataFrame({"date": dates, "symbol": "AUSDT"})
    return panel, close, open_, quote_volume, universe, scores


def _policy(**changes: object) -> XSectMomentumPolicy:
    base = XSectMomentumPolicy(
        short_lb=1,
        long_lb=1,
        skip=0,
        rebalance_days=2,
        top_k=1,
        n_short=1,
        weighting="equal",
        vol_lb=5,
        gross_exposure=1.0,
        liquidity_lb=1,
        min_age_days=0,
        execution_delay_days=1,
        fee_bps=0.0,
        half_spread_bps=0.0,
        base_slippage_bps=0.0,
        impact_coef_bps=0.0,
        funding_bps_per_day=0.0,
    )
    return replace(base, **changes)


def _always_long(
    universe: list[str],
    scores: pd.Series,
    previous: set[str],
    context: bt.SelectCtx,
) -> dict[str, float]:
    del scores, previous, context
    return {universe[0]: 1.0}


def _always_short(
    universe: list[str],
    scores: pd.Series,
    previous: set[str],
    context: bt.SelectCtx,
) -> dict[str, float]:
    del scores, previous, context
    return {universe[0]: -1.0}


def test_rebalance_boundary_is_marked_once_and_equity_reconciles() -> None:
    prices = 100.0 * np.power(1.01, np.arange(16))
    panel, close, open_, quote_volume, universe, scores = _fixture(prices)

    result = bt.run_backtest(
        panel,
        close,
        open_,
        quote_volume,
        universe,
        scores,
        _always_long,
        _policy(),
        bidirectional=True,
    )

    assert result.daily_ret.index.is_unique
    assert result.equity.index.equals(result.daily_ret.index)
    reconstructed = 1_000.0 * (1.0 + result.daily_ret).cumprod()
    pd.testing.assert_series_equal(result.equity, reconstructed, check_names=False)

    first_entry = result.daily_ret.index[0]
    expected = 1_000.0 * close.loc[result.daily_ret.index[-1], "AUSDT"] / open_.loc[first_entry, "AUSDT"]
    assert result.equity.iloc[-1] == pytest.approx(expected)
    assert result.daily_ret.iloc[2] == pytest.approx(0.01)
    assert result.trades["pnl_net"].sum() == pytest.approx(expected - 1_000.0)
    assert evaluate_gates(result).values["total_return"] == pytest.approx(expected / 1_000.0 - 1.0)


def test_entry_cost_is_in_daily_return_and_not_recharged_without_turnover() -> None:
    panel, close, open_, quote_volume, universe, scores = _fixture(np.full(16, 100.0))
    result = bt.run_backtest(
        panel,
        close,
        open_,
        quote_volume,
        universe,
        scores,
        _always_long,
        _policy(fee_bps=10.0),
        bidirectional=True,
    )

    assert result.equity.iloc[-1] == pytest.approx(999.0)
    assert result.daily_ret.iloc[0] == pytest.approx(-0.001)
    assert result.turnover.iloc[0] == pytest.approx(1.0)
    assert result.turnover.iloc[1:].sum() == pytest.approx(0.0)
    reconstructed = 1_000.0 * (1.0 + result.daily_ret).cumprod()
    pd.testing.assert_series_equal(result.equity, reconstructed, check_names=False)


def test_fixed_percent_stop_is_rejected() -> None:
    panel, close, open_, quote_volume, universe, scores = _fixture(np.full(16, 100.0))

    with pytest.raises(ValueError, match="fixed-percent stops"):
        bt.run_backtest(
            panel,
            close,
            open_,
            quote_volume,
            universe,
            scores,
            _always_long,
            _policy(stop_loss_pct=0.25),
            bidirectional=True,
        )


def test_terminal_missing_bar_is_an_explicit_forced_exit() -> None:
    prices = np.full(17, 100.0)
    prices[14:] = np.nan
    panel, close, open_, quote_volume, universe, scores = _fixture(prices)
    universe.iloc[14:] = False

    result = bt.run_backtest(
        panel,
        close,
        open_,
        quote_volume,
        universe,
        scores,
        _always_long,
        _policy(),
        bidirectional=True,
    )

    assert result.meta["forced_exit_count"] == 1
    assert "missing_bar_forced_exit" in set(result.trades["exit_reason"])
    assert result.equity.iloc[-1] == pytest.approx(1_000.0)


def test_loss_beyond_capital_terminates_at_bankruptcy() -> None:
    prices = np.full(16, 100.0)
    prices[12:] = 250.0
    panel, close, open_, quote_volume, universe, scores = _fixture(prices)

    result = bt.run_backtest(
        panel,
        close,
        open_,
        quote_volume,
        universe,
        scores,
        _always_short,
        _policy(),
        bidirectional=True,
    )

    assert result.meta["bankrupt"] is True
    assert result.equity.iloc[-1] == 0.0
    assert result.daily_ret.iloc[-1] == -1.0
    assert result.trades.iloc[-1]["exit_reason"] == "bankruptcy_at_open"
    assert evaluate_gates(result).values["g7_max_drawdown"] == pytest.approx(1.0)
