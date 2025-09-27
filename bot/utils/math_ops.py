"""Mathematical helper functions."""
from __future__ import annotations

from typing import Iterable

_EPSILON = 1e-12


def safe_div(numerator: float, denominator: float) -> float:
    if abs(denominator) <= _EPSILON:
        return 0.0
    return numerator / denominator


def median(values: Iterable[float]) -> float:
    sorted_values = sorted(values)
    if not sorted_values:
        return 0.0
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2 == 1:
        return float(sorted_values[mid])
    return float((sorted_values[mid - 1] + sorted_values[mid]) / 2)


def to_percent(value: float) -> float:
    return value * 100.0
