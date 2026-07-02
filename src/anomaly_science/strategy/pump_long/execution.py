"""Long-side structural path simulator over closed 1m bars.

Mirrors the pessimistic execution semantics of the generic trade simulator in
`anomaly_science/simulation/builder.py` (next-1m-open entry with slippage,
stop-checked-before-trailing within each bar, confirmed-swing-low trailing
with the registry-standard pivot window, CLOSE_BEYOND exits at the bar close),
re-implemented over numpy arrays so 39k events stream through the pump-fade
symbol cache without object-per-candle overhead. Parity is locked by unit
tests, not by imports: the originals are module-private and typed against the
MVP1 candle dataclasses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from anomaly_science.strategy.pump_long.spec import PumpLongExecutionSpec

EXIT_REASON_TRAILING_STOP = "trailing_stop"
EXIT_REASON_INITIAL_STOP = "initial_stop"
EXIT_REASON_DATA_END_CENSORED = "data_end_censored"
STATUS_FILLED = "filled"
STATUS_INVALID_STOP_ABOVE_ENTRY = "invalid_stop_above_entry"


@dataclass(frozen=True, slots=True)
class PumpLongTradeResult:
    status: str
    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float
    exit_reason: str
    initial_stop_price: float
    final_stop_price: float
    stop_distance_fraction: float
    gross_return: float
    net_return: float
    net_r: float
    mfe_return: float
    holding_minutes: int


def simulate_pump_long_trade(
    *,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    entry_index: int,
    initial_stop_price: float,
    spec: PumpLongExecutionSpec,
    giveback_fraction: float | None = None,
    giveback_arm_return: float = 0.0,
    breakeven_arm_return: float | None = None,
    take_profit_return: float | None = None,
) -> PumpLongTradeResult:
    """Simulate one long from the next-1m-open fill until structure breaks.

    `entry_index` is the bar whose OPEN is the fill (the first bar after the
    closed decision bar). Two realizable exit families are supported, both
    causal (they only use the peak reached so far, never a future peak):

    - swing-low trail (default, `giveback_fraction is None`): trail a confirmed
      swing low and exit on close-beyond. This is the tight v1 exit.
    - peak-giveback trail (`giveback_fraction` set): once the trade is up by at
      least `giveback_arm_return`, exit when the close retraces
      `giveback_fraction` off the highest close reached since entry. Before the
      trail arms, only the initial hard stop protects the position. This caps
      how much of the favorable excursion is given back and is the direct lever
      for capture ratio.

    If the data ends first the position is closed at the final close and marked
    censored.
    """

    if not 0 <= entry_index < len(close):
        raise ValueError("entry_index must address an existing bar")
    entry_open = float(open_[entry_index])
    entry_price = entry_open * (1.0 + spec.entry_slippage_bps / 10_000.0)
    if not math.isfinite(initial_stop_price) or initial_stop_price >= entry_open:
        return _unfilled(entry_index, entry_price, initial_stop_price)

    stop_price = float(initial_stop_price)
    stop_moved = False
    confirmation = spec.swing_confirmation_bars
    exit_index: int | None = None
    exit_raw: float | None = None
    highest_high = entry_open
    peak_close = entry_open
    armed = False
    for offset in range(entry_index, len(close)):
        bar_close = float(close[offset])
        highest_high = max(highest_high, float(high[offset]))
        stop_hit = (
            bar_close <= stop_price
            if spec.stop_trigger_close_beyond
            else float(low[offset]) <= stop_price
        )
        if stop_hit:
            exit_index = offset
            exit_raw = bar_close if spec.stop_trigger_close_beyond else stop_price
            break
        if take_profit_return is not None:
            tp_price = entry_open * (1.0 + take_profit_return)
            if float(high[offset]) >= tp_price:
                exit_index = offset
                exit_raw = tp_price
                break
        if breakeven_arm_return is not None and (
            float(high[offset]) / entry_open - 1.0
        ) >= breakeven_arm_return and stop_price < entry_open:
            stop_price = entry_open
            stop_moved = True
        if giveback_fraction is not None:
            peak_close = max(peak_close, bar_close)
            if not armed and (peak_close / entry_open - 1.0) >= giveback_arm_return:
                armed = True
            if armed and bar_close <= peak_close * (1.0 - giveback_fraction):
                exit_index = offset
                exit_raw = bar_close
                stop_moved = True
                break
        else:
            relative = offset - entry_index
            if relative >= 2 * confirmation:
                pivot_index = offset - confirmation
                window_start = pivot_index - confirmation
                pivot_low = float(low[pivot_index])
                if pivot_low == float(np.min(low[window_start : offset + 1])) and (
                    stop_price < pivot_low < bar_close
                ):
                    stop_price = pivot_low
                    stop_moved = True
    if exit_index is None:
        exit_index = len(close) - 1
        exit_raw = float(close[exit_index])
        exit_reason = EXIT_REASON_DATA_END_CENSORED
    else:
        exit_reason = EXIT_REASON_TRAILING_STOP if stop_moved else EXIT_REASON_INITIAL_STOP

    exit_price = float(exit_raw) * (1.0 - spec.exit_slippage_bps / 10_000.0)
    fee_rate = spec.fee_per_side_bps / 10_000.0
    total_cost = entry_price * fee_rate + exit_price * fee_rate
    gross_pnl = exit_price - entry_price
    net_pnl = gross_pnl - total_cost
    stop_distance = entry_price - float(initial_stop_price)
    return PumpLongTradeResult(
        status=STATUS_FILLED,
        entry_index=entry_index,
        exit_index=exit_index,
        entry_price=entry_price,
        exit_price=exit_price,
        exit_reason=exit_reason,
        initial_stop_price=float(initial_stop_price),
        final_stop_price=stop_price,
        stop_distance_fraction=stop_distance / entry_price,
        gross_return=gross_pnl / entry_price,
        net_return=net_pnl / entry_price,
        net_r=net_pnl / stop_distance,
        mfe_return=(highest_high - entry_price) / entry_price,
        holding_minutes=exit_index - entry_index + 1,
    )


def _unfilled(entry_index: int, entry_price: float, initial_stop_price: float) -> PumpLongTradeResult:
    return PumpLongTradeResult(
        status=STATUS_INVALID_STOP_ABOVE_ENTRY,
        entry_index=entry_index,
        exit_index=entry_index,
        entry_price=entry_price,
        exit_price=float("nan"),
        exit_reason="not_filled",
        initial_stop_price=float(initial_stop_price),
        final_stop_price=float(initial_stop_price),
        stop_distance_fraction=float("nan"),
        gross_return=float("nan"),
        net_return=float("nan"),
        net_r=float("nan"),
        mfe_return=float("nan"),
        holding_minutes=0,
    )


def first_retrace_kill_index(
    *,
    close: np.ndarray,
    high: np.ndarray,
    ignition_index: int,
    end_index: int,
    base: float,
    spec: PumpLongExecutionSpec,
) -> int | None:
    """Return the bar index where "the train has left" is first CONFIRMED.

    The kill level is `base + fraction * (running_high - base)` with the
    running high evolving bar by bar. A single close below the level is noise;
    the break is confirmed only after a consecutive run of below-level closes
    lasting at least max(min_minutes, elapsed_fraction * pump_elapsed_min at
    the start of the run). Young pumps therefore tolerate the registered
    minimum of noise while mature pumps require a proportionally longer break.
    Entries at decisions strictly BEFORE the returned index remain eligible.
    """

    running_high = float(high[ignition_index])
    run_start: int | None = None
    for offset in range(ignition_index, min(end_index, len(close) - 1) + 1):
        running_high = max(running_high, float(high[offset]))
        if running_high <= base:
            run_start = None
            continue
        kill_level = base + spec.retrace_kill_fraction * (running_high - base)
        if float(close[offset]) < kill_level:
            if run_start is None:
                run_start = offset
            elapsed_at_run_start = run_start - ignition_index + 1
            required = max(
                spec.retrace_kill_min_minutes,
                math.ceil(spec.retrace_kill_elapsed_fraction * elapsed_at_run_start),
            )
            if offset - run_start + 1 >= required:
                return offset
        else:
            run_start = None
    return None


def last_confirmed_swing_low(
    *,
    low: np.ndarray,
    start_index: int,
    decision_index: int,
    confirmation_bars: int,
) -> float | None:
    """Latest swing low confirmed by closed bars at or before the decision bar.

    Uses the same pivot rule as the trailing logic: the pivot bar's low must be
    the minimum of the window [pivot - k, pivot + k]. Only bars up to
    `decision_index` (a closed bar at snapshot time) participate, so the level
    is causally known at entry.
    """

    for pivot_index in range(decision_index - confirmation_bars, start_index - 1, -1):
        window_start = pivot_index - confirmation_bars
        if window_start < start_index:
            break
        window = low[window_start : pivot_index + confirmation_bars + 1]
        pivot_low = float(low[pivot_index])
        if pivot_low == float(np.min(window)):
            return pivot_low
    return None


__all__ = [
    "EXIT_REASON_DATA_END_CENSORED",
    "EXIT_REASON_INITIAL_STOP",
    "EXIT_REASON_TRAILING_STOP",
    "PumpLongTradeResult",
    "STATUS_FILLED",
    "STATUS_INVALID_STOP_ABOVE_ENTRY",
    "first_retrace_kill_index",
    "last_confirmed_swing_low",
    "simulate_pump_long_trade",
]
