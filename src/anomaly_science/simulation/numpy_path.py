"""Strategy-neutral long-side structural path simulator over closed 1m bars.

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
from typing import Protocol

import numpy as np


class LongPathExecutionSpec(Protocol):
    """Mechanical values supplied by a strategy declaration."""

    entry_slippage_bps: float
    exit_slippage_bps: float
    fee_per_side_bps: float
    swing_confirmation_bars: int
    stop_trigger_close_beyond: bool
    retrace_kill_fraction: float
    retrace_kill_min_minutes: int
    retrace_kill_elapsed_fraction: float

EXIT_REASON_TRAILING_STOP = "trailing_stop"
EXIT_REASON_INITIAL_STOP = "initial_stop"
EXIT_REASON_DATA_END_CENSORED = "data_end_censored"
STATUS_FILLED = "filled"
STATUS_INVALID_STOP_ABOVE_ENTRY = "invalid_stop_above_entry"


@dataclass(frozen=True, slots=True)
class LongPathResult:
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


def simulate_long_path(
    *,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    entry_index: int,
    initial_stop_price: float,
    spec: LongPathExecutionSpec,
    giveback_fraction: float | None = None,
    giveback_arm_return: float = 0.0,
    breakeven_arm_return: float | None = None,
    take_profit_price: float | None = None,
    swing_reversal_atr: float | None = None,
    stage_switch_r: float | None = None,
    atr: np.ndarray | None = None,
) -> LongPathResult:
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

    `stage_switch_r` makes the stop trigger *stage-dependent* instead of using
    the fixed `spec.stop_trigger_close_beyond`. While the trade's favorable
    excursion (highest high above entry) is below `stage_switch_r` initial-R,
    the position is "early" and the stop only fires on a CLOSE beyond it
    (tolerant of intrabar noise while the move is unproven); once the peak
    reaches `stage_switch_r`-R the position is "late/de-risked" and the stop
    fires on the first intrabar TOUCH (locking in the run). This is the
    winning pump-long exit and pairs with the `swing_reversal_atr` trail.
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
    # structural swing-low trail state (ZigZag-style, ATR-confirmed pivots)
    pivot_low = float("inf")
    pivot_low_atr = float("nan")
    for offset in range(entry_index, len(close)):
        bar_close = float(close[offset])
        highest_high = max(highest_high, float(high[offset]))
        if stage_switch_r is not None:
            initial_r = entry_open - float(initial_stop_price)
            late = initial_r > 0.0 and (highest_high - entry_open) >= stage_switch_r * initial_r
            close_beyond = not late
        else:
            close_beyond = spec.stop_trigger_close_beyond
        stop_hit = (
            bar_close <= stop_price if close_beyond else float(low[offset]) <= stop_price
        )
        if stop_hit:
            exit_index = offset
            exit_raw = bar_close if close_beyond else stop_price
            break
        if take_profit_price is not None:
            if not math.isfinite(take_profit_price) or take_profit_price <= entry_open:
                raise ValueError("take_profit_price must be a structural price above the long entry")
            if float(high[offset]) >= take_profit_price:
                exit_index = offset
                exit_raw = take_profit_price
                break
        if breakeven_arm_return is not None and (
            float(high[offset]) / entry_open - 1.0
        ) >= breakeven_arm_return and stop_price < entry_open:
            stop_price = entry_open
            stop_moved = True
        if swing_reversal_atr is not None:
            # ZigZag-style structural trail: track the lowest low of the current
            # pullback; once price rallies at least swing_reversal_atr * ATR above
            # that low, it is a CONFIRMED swing low that every participant can see.
            # Ratchet the stop up to it (higher-lows only), then hunt the next one.
            bar_low = float(low[offset])
            if bar_low < pivot_low:
                pivot_low = bar_low
                pivot_low_atr = float(atr[offset]) if atr is not None else float("nan")
            if (
                math.isfinite(pivot_low_atr)
                and pivot_low_atr > 0.0
                and (float(high[offset]) - pivot_low) >= swing_reversal_atr * pivot_low_atr
            ):
                if stop_price < pivot_low < bar_close:
                    stop_price = pivot_low
                    stop_moved = True
                pivot_low = bar_low
                pivot_low_atr = float(atr[offset]) if atr is not None else float("nan")
        elif giveback_fraction is not None:
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
    return LongPathResult(
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


def _unfilled(entry_index: int, entry_price: float, initial_stop_price: float) -> LongPathResult:
    return LongPathResult(
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


def causal_atr(
    *, high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int = 30
) -> np.ndarray:
    """Wilder-style true-range average using only closed bars up to each index.

    Fully causal: atr[j] depends on bars <= j. Used to scale the swing-reversal
    threshold so "not microscopic" adapts to each symbol's own volatility.
    """

    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    prev_close = np.concatenate(([close[0]], close[:-1]))
    true_range = np.maximum.reduce(
        (high - low, np.abs(high - prev_close), np.abs(low - prev_close))
    )
    atr = np.full(len(true_range), np.nan)
    cumulative = 0.0
    for i in range(len(true_range)):
        cumulative += true_range[i]
        if i >= window:
            cumulative -= true_range[i - window]
            atr[i] = cumulative / window
        else:
            atr[i] = cumulative / (i + 1)
    return atr


def first_retrace_kill_index(
    *,
    close: np.ndarray,
    high: np.ndarray,
    ignition_index: int,
    end_index: int,
    base: float,
    spec: LongPathExecutionSpec,
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
    "LongPathResult",
    "STATUS_FILLED",
    "STATUS_INVALID_STOP_ABOVE_ENTRY",
    "causal_atr",
    "first_retrace_kill_index",
    "last_confirmed_swing_low",
    "simulate_long_path",
]
