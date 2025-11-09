from __future__ import annotations

import math
from typing import Iterable, Sequence

from config.config import AtrConfig
from domain.models.candle import Candle


def _percentile_from_sorted(sorted_values: Sequence[float], percentile: float) -> float:
    if not sorted_values:
        raise ValueError("sorted_values must not be empty")
    if percentile <= 0:
        return sorted_values[0]
    if percentile >= 100:
        return sorted_values[-1]
    fraction = percentile / 100.0
    position = (len(sorted_values) - 1) * fraction
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[lower_index]
    lower_value = sorted_values[lower_index]
    upper_value = sorted_values[upper_index]
    weight = position - lower_index
    return lower_value + (upper_value - lower_value) * weight


def _rolling_percentile_bounds(values: Sequence[float], window: int, percentile_low: float, percentile_high: float) -> list[tuple[float, float]]:
    bounds: list[tuple[float, float]] = []
    for index in range(len(values)):
        start = max(0, index + 1 - window)
        window_slice = sorted(values[start : index + 1])
        low = _percentile_from_sorted(window_slice, percentile_low)
        high = _percentile_from_sorted(window_slice, percentile_high)
        bounds.append((low, high))
    return bounds


def _compute_true_range(candles: Sequence[Candle]) -> list[float]:
    true_ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        high_low = candle.high - candle.low
        if previous_close is None:
            true_range = high_low
        else:
            high_close = abs(candle.high - previous_close)
            low_close = abs(candle.low - previous_close)
            true_range = max(high_low, high_close, low_close)
        true_ranges.append(true_range)
        previous_close = candle.close
    return true_ranges


def _winsorize(values: Sequence[float], bounds: Iterable[tuple[float, float]]) -> list[float]:
    clipped: list[float] = []
    for value, (lower, upper) in zip(values, bounds):
        clipped.append(min(max(value, lower), upper))
    return clipped


def _compute_ema(values: Sequence[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("EMA period must be positive")
    ema_values: list[float] = []
    alpha = 2.0 / (period + 1.0)
    ema: float | None = None
    for value in values:
        if ema is None:
            ema = value
        else:
            ema = ema + alpha * (value - ema)
        ema_values.append(ema)
    return ema_values


def compute_atr(candles: Sequence[Candle], config: AtrConfig) -> list[float]:
    """Compute ATR values for the provided candles."""
    if not candles:
        return []

    true_ranges = _compute_true_range(candles)
    window_size = max(1, math.ceil(config.atr_period * config.winsor_mult))
    bounds = _rolling_percentile_bounds(true_ranges, window_size, config.p_low, config.p_high)
    clipped = _winsorize(true_ranges, bounds)
    return _compute_ema(clipped, config.atr_period)


__all__ = ["compute_atr"]
