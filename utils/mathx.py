"""Mathematical helpers for the trading domain."""

from __future__ import annotations

import math
from typing import Sequence, Tuple


def median_filter_of_three(values: Sequence[float]) -> Tuple[float, ...]:
    """Return a sequence smoothed by a median-of-three filter."""

    length: int = len(values)
    if length == 0:
        return ()
    if length < 3:
        return tuple(float(value) for value in values)
    smoothed: list[float] = [float(values[0])]
    for index in range(1, length - 1):
        window: Tuple[float, float, float] = (
            float(values[index - 1]),
            float(values[index]),
            float(values[index + 1]),
        )
        sorted_window: list[float] = sorted(window)
        smoothed.append(sorted_window[1])
    smoothed.append(float(values[-1]))
    return tuple(smoothed)


def compute_odr_weight(price: float, reference_price: float, tick_size: float) -> float:
    """Return a decay weight based on distance between two prices."""

    if tick_size <= 0.0:
        raise ValueError("tick_size must be positive")
    distance_ticks: float = abs(price - reference_price) / tick_size
    return 1.0 / (1.0 + distance_ticks)


def floor_to_step(value: float, step: float) -> float:
    if step <= 0.0:
        raise ValueError("step must be positive")
    scaled: float = value / step
    floored: float = math.floor(scaled)
    return floored * step


def ceil_to_step(value: float, step: float) -> float:
    if step <= 0.0:
        raise ValueError("step must be positive")
    scaled: float = value / step
    ceiled: float = math.ceil(scaled)
    return ceiled * step


def round_to_step(value: float, step: float) -> float:
    if step <= 0.0:
        raise ValueError("step must be positive")
    scaled: float = value / step
    if scaled >= 0.0:
        rounded: float = math.floor(scaled + 0.5)
    else:
        rounded = math.ceil(scaled - 0.5)
    return rounded * step


def compute_position_size(
    balance: float,
    fraction: float,
    minimum_notional: float,
    price: float,
    step: float,
) -> float:
    if price <= 0.0:
        raise ValueError("price must be positive")
    if step <= 0.0:
        raise ValueError("step must be positive")
    if fraction < 0.0:
        raise ValueError("fraction must be non-negative")
    if minimum_notional < 0.0:
        raise ValueError("minimum_notional must be non-negative")
    if balance <= 0.0:
        return 0.0
    target_notional: float = max(balance * fraction, minimum_notional)
    if target_notional <= 0.0:
        return 0.0
    raw_quantity: float = target_notional / price
    if raw_quantity <= 0.0:
        return 0.0
    return floor_to_step(raw_quantity, step)


__all__ = [
    "median_filter_of_three",
    "compute_odr_weight",
    "floor_to_step",
    "ceil_to_step",
    "round_to_step",
    "compute_position_size",
]
