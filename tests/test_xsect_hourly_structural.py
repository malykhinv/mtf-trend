from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from anomaly_science.strategy.xsect_momentum.research import backtest as bt
from anomaly_science.strategy.xsect_momentum.research.hourly_structural import (
    HourBar,
    HourlyBars,
    HourlyBarStore,
    StructuralAnchorSpec,
    confirmed_structural_anchor,
    run_hourly_backtest,
    stop_fill_price,
)
from anomaly_science.strategy.xsect_momentum.research.policy import XSectMomentumPolicy


def _bars(high: np.ndarray, low: np.ndarray | None = None) -> HourlyBars:
    dates = pd.date_range("2025-01-01", periods=len(high), freq="h", tz="UTC")
    base = np.full(len(high), 100.0)
    lows = np.full(len(high), 99.0) if low is None else low
    return HourlyBars(
        timestamp_ns=dates.astype("int64").to_numpy(),
        open=base.copy(),
        high=high.astype(float),
        low=lows.astype(float),
        close=base.copy(),
    )


def test_anchor_uses_only_pivot_confirmed_before_entry() -> None:
    high = np.full(20, 101.0)
    high[10] = 110.0  # confirmed before entry
    high[14] = 120.0  # attractive, but not right-confirmed at entry index 15
    bars = _bars(high)
    entry = pd.Timestamp("2025-01-01 15:00", tz="UTC")

    anchor = confirmed_structural_anchor(
        bars,
        entry,
        entry_price=100.0,
        side="short",
        spec=StructuralAnchorSpec(left_hours=2, right_hours=2, lookback_hours=20),
    )

    assert anchor is not None
    assert anchor.price == 110.0
    assert anchor.pivot_time == pd.Timestamp("2025-01-01 10:00", tz="UTC")
    assert anchor.confirmed_time <= entry


def test_structural_stop_touch_and_gap_fills_are_pessimistic() -> None:
    touch = HourBar(
        pd.Timestamp("2025-01-01", tz="UTC"),
        open=100.0,
        high=111.0,
        low=99.0,
        close=105.0,
    )
    gap = replace(touch, open=115.0, high=118.0, low=114.0, close=116.0)

    assert stop_fill_price(-0.5, 110.0, touch) == 110.0
    assert stop_fill_price(-0.5, 110.0, gap) == 115.0

    long_touch = replace(touch, open=100.0, high=101.0, low=89.0, close=95.0)
    long_gap = replace(long_touch, open=85.0, high=87.0, low=82.0, close=84.0)
    assert stop_fill_price(+0.5, 90.0, long_touch) == 90.0
    assert stop_fill_price(+0.5, 90.0, long_gap) == 85.0


def _two_side_selector(
    universe: list[str],
    scores: pd.Series,
    previous: set[str],
    context: bt.SelectCtx,
) -> dict[str, float]:
    del universe, scores, previous, context
    # Net gross is deliberately below one, as happens after overlapping
    # long/short selections cancel. The hourly engine must not lever it back up.
    return {"AUSDT": 0.35, "BUSDT": -0.25}


def test_hourly_no_stop_reconciles_to_daily_engine(tmp_path) -> None:
    days = pd.date_range("2025-01-01", periods=20, tz="UTC")
    hours = pd.date_range(days[0], periods=20 * 24, freq="h", tz="UTC")
    hour_number = np.arange(len(hours))
    hourly_prices = {
        "AUSDT": 100.0 * np.power(1.0002, hour_number),
        "BUSDT": np.full(len(hours), 100.0),
    }
    for symbol, opens in hourly_prices.items():
        closes = opens * (1.0002 if symbol == "AUSDT" else 1.0)
        frame = pd.DataFrame({
            "timestamp": hours.astype("int64") // 1_000_000,
            "open": opens,
            "high": np.maximum(opens, closes),
            "low": np.minimum(opens, closes),
            "close": closes,
        })
        frame.to_parquet(tmp_path / f"{symbol}.parquet")

    open_daily = pd.DataFrame(
        {s: [v[i * 24] for i in range(20)] for s, v in hourly_prices.items()},
        index=days,
    )
    close_daily = pd.DataFrame(
        {
            "AUSDT": [hourly_prices["AUSDT"][(i + 1) * 24 - 1] * 1.0002 for i in range(20)],
            "BUSDT": 100.0,
        },
        index=days,
    )
    quote_volume = pd.DataFrame(1_000_000.0, index=days, columns=["AUSDT", "BUSDT"])
    universe = pd.DataFrame(True, index=days, columns=["AUSDT", "BUSDT"])
    scores = pd.DataFrame({"AUSDT": 1.0, "BUSDT": 0.0}, index=days)
    panel = pd.DataFrame({"date": np.repeat(days, 2), "symbol": ["AUSDT", "BUSDT"] * 20})
    policy = XSectMomentumPolicy(
        short_lb=1,
        long_lb=1,
        skip=0,
        rebalance_days=3,
        top_k=1,
        n_short=1,
        weighting="equal",
        vol_lb=5,
        liquidity_lb=1,
        min_age_days=0,
        execution_delay_days=1,
        fee_bps=0.0,
        half_spread_bps=0.0,
        base_slippage_bps=0.0,
        impact_coef_bps=0.0,
        funding_bps_per_day=0.0,
    )

    daily = bt.run_backtest(
        panel,
        close_daily,
        open_daily,
        quote_volume,
        universe,
        scores,
        _two_side_selector,
        policy,
        bidirectional=True,
    )
    hourly = run_hourly_backtest(
        panel,
        close_daily,
        quote_volume,
        universe,
        scores,
        _two_side_selector,
        policy,
        HourlyBarStore(tmp_path, pd.Timestamp("2026-01-01", tz="UTC")),
        "no_stop",
    )

    assert hourly.equity.iloc[-1] == pytest.approx(daily.equity.iloc[-1], rel=1e-12)
    pd.testing.assert_series_equal(hourly.daily_ret, daily.daily_ret, check_names=False, rtol=1e-12)
    reconstructed = policy.start_equity * (1.0 + hourly.daily_ret).cumprod()
    pd.testing.assert_series_equal(hourly.equity, reconstructed, check_names=False, rtol=1e-12)
