from __future__ import annotations

import numpy as np
import pytest

from anomaly_science.strategy.pump_long.execution import (
    EXIT_REASON_DATA_END_CENSORED,
    EXIT_REASON_INITIAL_STOP,
    EXIT_REASON_TRAILING_STOP,
    STATUS_FILLED,
    STATUS_INVALID_STOP_ABOVE_ENTRY,
    first_retrace_kill_index,
    last_confirmed_swing_low,
    simulate_pump_long_trade,
)
from anomaly_science.strategy.pump_long.spec import PumpLongExecutionSpec


def _frictionless(**overrides) -> PumpLongExecutionSpec:
    params = dict(entry_slippage_bps=0.0, exit_slippage_bps=0.0, fee_per_side_bps=0.0)
    params.update(overrides)
    return PumpLongExecutionSpec(**params)


def test_trailing_stop_ratchets_to_confirmed_swing_low_and_exits_on_close_beyond() -> None:
    # Pivot low 98 at index 2 confirms at index 4 (window [0..4]); bar 5 closes
    # through the trailed stop and exits at the bar close (pessimistic).
    open_ = np.asarray([100.0, 101.0, 102.0, 103.0, 104.0, 104.0])
    high = np.asarray([101.5, 102.5, 103.5, 104.5, 105.5, 104.5])
    low = np.asarray([100.0, 99.0, 98.0, 99.0, 100.0, 96.5])
    close = np.asarray([101.0, 102.0, 103.0, 104.0, 105.0, 97.0])

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=95.0, spec=_frictionless(),
    )

    assert result.status == STATUS_FILLED
    assert result.entry_price == pytest.approx(100.0)
    assert result.final_stop_price == pytest.approx(98.0)
    assert result.exit_reason == EXIT_REASON_TRAILING_STOP
    assert result.exit_index == 5
    assert result.exit_price == pytest.approx(97.0)
    assert result.gross_return == pytest.approx(-0.03)
    assert result.net_r == pytest.approx(-3.0 / 5.0)
    assert result.mfe_return == pytest.approx(5.5 / 100.0)
    assert result.stop_distance_fraction == pytest.approx(0.05)


def test_stop_is_checked_before_trailing_update_within_the_same_bar() -> None:
    # At index 2 (confirmation bar for pivot 1) the close is already through
    # the initial stop: the trade must exit there, not trail first.
    open_ = np.asarray([100.0, 101.0, 100.0])
    high = np.asarray([101.0, 102.0, 100.5])
    low = np.asarray([99.5, 99.2, 94.0])
    close = np.asarray([101.0, 101.5, 94.5])

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=95.0,
        spec=_frictionless(swing_confirmation_bars=1),
    )

    assert result.exit_reason == EXIT_REASON_INITIAL_STOP
    assert result.final_stop_price == pytest.approx(95.0)
    assert result.exit_price == pytest.approx(94.5)


def test_costs_and_slippage_are_charged_on_both_sides() -> None:
    open_ = np.asarray([100.0, 95.0])
    high = np.asarray([100.5, 95.5])
    low = np.asarray([99.0, 89.0])
    close = np.asarray([99.5, 90.0])

    spec = PumpLongExecutionSpec(entry_slippage_bps=5.0, exit_slippage_bps=5.0, fee_per_side_bps=10.0)
    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=92.0, spec=spec,
    )

    entry = 100.0 * 1.0005
    exit_ = 90.0 * 0.9995  # bar 1 closes through the 92.0 stop
    cost = (entry + exit_) * 0.001
    assert result.exit_index == 1
    assert result.entry_price == pytest.approx(entry)
    assert result.exit_price == pytest.approx(exit_)
    assert result.net_return == pytest.approx((exit_ - entry - cost) / entry)


def test_touch_trigger_exits_at_stop_price() -> None:
    open_ = np.asarray([100.0, 101.0])
    high = np.asarray([101.0, 102.0])
    low = np.asarray([99.5, 94.0])
    close = np.asarray([100.5, 101.5])  # close never below the stop

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=95.0,
        spec=_frictionless(stop_trigger_close_beyond=False),
    )

    assert result.exit_index == 1
    assert result.exit_price == pytest.approx(95.0)


def test_stop_above_entry_open_is_not_filled() -> None:
    open_ = np.asarray([100.0, 101.0])
    high = np.asarray([101.0, 102.0])
    low = np.asarray([99.0, 100.0])
    close = np.asarray([100.5, 101.5])

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=100.5, spec=_frictionless(),
    )

    assert result.status == STATUS_INVALID_STOP_ABOVE_ENTRY
    assert np.isnan(result.net_return)


def test_position_surviving_to_data_end_is_censored_at_final_close() -> None:
    open_ = np.asarray([100.0, 101.0, 102.0])
    high = np.asarray([101.0, 102.0, 103.0])
    low = np.asarray([99.5, 100.5, 101.5])
    close = np.asarray([101.0, 102.0, 103.0])

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=95.0, spec=_frictionless(),
    )

    assert result.exit_reason == EXIT_REASON_DATA_END_CENSORED
    assert result.exit_price == pytest.approx(103.0)


def test_retrace_kill_requires_confirmed_consecutive_closes_below_half_height() -> None:
    # base=100, running high 110 => kill level 105. Two below-level closes are
    # noise (young pump needs 3); a later 3-bar run confirms at its third bar.
    base = 100.0
    high = np.full(10, 110.0)
    close = np.asarray([106.0, 104.0, 104.5, 106.0, 104.0, 104.5, 104.9, 106.0, 106.0, 106.0])

    kill = first_retrace_kill_index(
        close=close, high=high, ignition_index=0, end_index=9, base=base,
        spec=PumpLongExecutionSpec(),
    )

    assert kill == 6  # run starts at 4, third consecutive below-close at 6


def test_retrace_kill_scales_with_pump_age() -> None:
    # Run starts 50 bars after ignition => required = max(3, ceil(0.1*51)) = 6.
    base = 100.0
    n = 60
    high = np.full(n, 110.0)
    close = np.full(n, 106.0)
    close[50:55] = 104.0  # 5 consecutive below-closes: not enough
    spec = PumpLongExecutionSpec()

    assert first_retrace_kill_index(
        close=close, high=high, ignition_index=0, end_index=n - 1, base=base, spec=spec
    ) is None

    close[55] = 104.0  # sixth consecutive below-close confirms
    assert first_retrace_kill_index(
        close=close, high=high, ignition_index=0, end_index=n - 1, base=base, spec=spec
    ) == 55


def test_last_confirmed_swing_low_uses_only_closed_bars() -> None:
    low = np.asarray([100.0, 98.0, 99.0, 100.0, 97.0, 99.0, 100.0])

    level = last_confirmed_swing_low(low=low, start_index=0, decision_index=6, confirmation_bars=2)
    assert level == pytest.approx(97.0)

    assert last_confirmed_swing_low(
        low=np.asarray([100.0, 101.0, 102.0]), start_index=0, decision_index=2, confirmation_bars=2
    ) is None
