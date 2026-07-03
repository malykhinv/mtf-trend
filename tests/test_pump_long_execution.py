from __future__ import annotations

import numpy as np
import pytest

from anomaly_science.strategy.pump_long.execution import (
    EXIT_REASON_DATA_END_CENSORED,
    EXIT_REASON_INITIAL_STOP,
    EXIT_REASON_TRAILING_STOP,
    STATUS_FILLED,
    STATUS_INVALID_STOP_ABOVE_ENTRY,
    causal_atr,
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


def test_peak_giveback_exit_caps_retrace_off_the_running_peak() -> None:
    # Rises to close 120 (peak), then a bar closes at 108 = 10% off the peak.
    # With a 10% giveback and immediate arming, exit fires at that bar close.
    open_ = np.asarray([100.0, 110.0, 118.0, 120.0, 108.0])
    high = np.asarray([101.0, 112.0, 119.0, 121.0, 119.0])
    low = np.asarray([99.5, 109.0, 117.0, 118.0, 107.0])
    close = np.asarray([110.0, 118.0, 120.0, 119.0, 108.0])

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=95.0, spec=_frictionless(),
        giveback_fraction=0.10, giveback_arm_return=0.0,
    )

    assert result.exit_index == 4
    assert result.exit_price == pytest.approx(108.0)
    assert result.net_return == pytest.approx(0.08)  # 100 -> 108, frictionless


def test_peak_giveback_stays_in_until_armed() -> None:
    # A 3% dip on bar 1 would trigger a 2% giveback, but arming needs +5% first,
    # so the position is held (only the hard stop is live) and rides to the top.
    open_ = np.asarray([100.0, 100.0, 106.0, 112.0])
    high = np.asarray([100.5, 100.5, 107.0, 113.0])
    low = np.asarray([99.0, 96.5, 105.0, 111.0])
    close = np.asarray([100.0, 97.0, 106.0, 112.0])

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=95.0, spec=_frictionless(),
        giveback_fraction=0.02, giveback_arm_return=0.05,
    )

    assert result.exit_reason == EXIT_REASON_DATA_END_CENSORED
    assert result.exit_price == pytest.approx(112.0)


def test_take_profit_fills_at_the_limit_when_high_touches_it() -> None:
    open_ = np.asarray([100.0, 108.0, 120.0])
    high = np.asarray([101.0, 112.0, 121.0])  # bar 1 high 112 >= TP 110
    low = np.asarray([99.5, 107.0, 118.0])
    close = np.asarray([100.5, 111.0, 120.0])

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=95.0, spec=_frictionless(),
        take_profit_price=110.0,
    )

    assert result.exit_index == 1
    assert result.exit_price == pytest.approx(110.0)
    assert result.net_return == pytest.approx(0.10)


def test_breakeven_move_caps_a_reversing_trade_near_entry() -> None:
    # Pops +6% (arms breakeven at +5%), then reverses; exit at the breakeven
    # stop close instead of bleeding to the deep initial stop.
    open_ = np.asarray([100.0, 106.0, 99.0])
    high = np.asarray([100.5, 106.5, 100.0])
    low = np.asarray([99.5, 105.0, 98.0])
    close = np.asarray([106.0, 105.5, 99.5])

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=90.0, spec=_frictionless(),
        breakeven_arm_return=0.05,
    )

    assert result.final_stop_price == pytest.approx(100.0)
    assert result.exit_index == 2  # bar-2 close 99.5 < breakeven 100
    assert result.net_return == pytest.approx(-0.005)  # ~flat, not -10%


def test_structural_swing_trail_ratchets_on_atr_confirmed_higher_lows() -> None:
    # ATR ~1. Dip to 98 forms a pivot; rally to >=100 (2*ATR above 98) confirms
    # it -> stop ratchets to 98. Then a higher low 103 confirms on a rally to
    # >=105 -> stop 103. Finally close below 103 exits at that structure.
    open_ = np.asarray([100.0, 99.0, 98.0, 101.0, 104.0, 103.0, 106.0, 102.0])
    high = np.asarray([100.5, 99.5, 98.5, 101.5, 104.5, 103.5, 106.5, 104.0])
    low = np.asarray([99.5, 98.5, 98.0, 100.0, 103.0, 102.8, 105.0, 101.0])
    close = np.asarray([100.0, 99.0, 98.2, 101.0, 104.0, 103.2, 106.0, 102.0])
    atr = np.ones(len(close))

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=90.0, spec=_frictionless(),
        swing_reversal_atr=2.0, atr=atr,
    )

    assert result.exit_reason == EXIT_REASON_TRAILING_STOP
    assert result.final_stop_price >= 98.0  # ratcheted up off the deep base
    assert result.final_stop_price > 90.0


def test_stage_switch_early_tolerates_intrabar_dip_below_stop() -> None:
    # entry 100, stop 90 => R=10, stage_switch_r=2 => "late" only once the peak
    # reaches 120. While early, an intrabar low of 89 (below the stop) that
    # closes back at 95 must NOT stop the trade (close-beyond tolerance).
    open_ = np.asarray([100.0, 100.0])
    high = np.asarray([101.0, 96.0])
    low = np.asarray([99.0, 89.0])
    close = np.asarray([100.0, 95.0])

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=90.0, spec=_frictionless(),
        stage_switch_r=2.0,
    )

    assert result.exit_reason == EXIT_REASON_DATA_END_CENSORED
    assert result.exit_price == pytest.approx(95.0)


def test_stage_switch_late_exits_on_first_intrabar_touch() -> None:
    # Same 100/90 (R=10, switch at peak>=120). Bar 1 highs 121 => de-risked
    # "late": bar 2's intrabar low 89 now TOUCHES the stop and exits at 90,
    # even though its close (95) is above the stop.
    open_ = np.asarray([100.0, 100.0, 100.0])
    high = np.asarray([101.0, 121.0, 110.0])
    low = np.asarray([99.0, 118.0, 89.0])
    close = np.asarray([100.0, 120.0, 95.0])

    result = simulate_pump_long_trade(
        open_=open_, high=high, low=low, close=close,
        entry_index=0, initial_stop_price=90.0, spec=_frictionless(),
        stage_switch_r=2.0,
    )

    assert result.exit_index == 2
    assert result.exit_price == pytest.approx(90.0)


def test_causal_atr_is_positive_and_causal() -> None:
    high = np.asarray([10.0, 11.0, 10.5, 12.0, 11.5])
    low = np.asarray([9.0, 9.5, 9.8, 10.5, 10.8])
    close = np.asarray([9.5, 10.5, 10.0, 11.5, 11.0])
    atr = causal_atr(high=high, low=low, close=close, window=3)
    assert len(atr) == 5
    assert np.all(atr > 0)


def test_last_confirmed_swing_low_uses_only_closed_bars() -> None:
    low = np.asarray([100.0, 98.0, 99.0, 100.0, 97.0, 99.0, 100.0])

    level = last_confirmed_swing_low(low=low, start_index=0, decision_index=6, confirmation_bars=2)
    assert level == pytest.approx(97.0)

    assert last_confirmed_swing_low(
        low=np.asarray([100.0, 101.0, 102.0]), start_index=0, decision_index=2, confirmation_bars=2
    ) is None
