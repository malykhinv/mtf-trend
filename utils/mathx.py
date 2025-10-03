from __future__ import annotations

"""Mathematical helpers for the trading domain."""

import math
from typing import Sequence, Tuple


def median_filter_of_three(values: Sequence[float]) -> Tuple[float, ...]:
    """Apply a median-of-three filter to the provided sequence."""

    length: int = len(values)
    if length == 0:
        return ()
    if length < 3:
        return tuple(values)
    smoothed: list[float] = [values[0]]
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


def price_weight(price: float, reference_price: float, tick_size: float) -> float:
    """Return distance weight for a level using reciprocal tick distance."""

    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    ticks: float = abs(price - reference_price) / tick_size
    return 1.0 / (1.0 + ticks)


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
    target_notional: float = max(balance * fraction, minimum_notional)
    raw_quantity: float = target_notional / price
    if raw_quantity <= 0.0:
        return 0.0
    return floor_to_step(raw_quantity, step)


__all__ = [
    "median_filter_of_three",
    "price_weight",
    "floor_to_step",
    "ceil_to_step",
    "compute_position_size",
]
