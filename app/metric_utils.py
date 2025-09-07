"""Utility functions for metric calculations."""
"""Utility functions for metric calculations."""

from typing import Deque

import constants


def update_ewma(
    value: float, mean: float, std: float, alpha: float
) -> tuple[float, float]:
    """Return updated EWMA mean and std for ``value``."""
    if mean == 0.0 and std == 0.0:
        return value, 0.0
    var = std**2
    delta = value - mean
    mean += alpha * delta
    var = (1 - alpha) * (var + alpha * delta * delta)
    return mean, var ** 0.5


def zscore(
    value: float, ewma_mean: float, ewma_std: float, window: Deque[float]
) -> float:
    """Return z-score of ``value`` using precomputed EWMA statistics."""
    if len(window) < constants.Z_BASE_WINDOW_MIN or ewma_std <= 0:
        return 0.0
    return (value - ewma_mean) / ewma_std


def zscore_window(value: float, window: Deque[float]) -> float:
    """Return z-score of ``value`` within ``window`` values."""
    if len(window) < constants.Z_BASE_WINDOW_MIN:
        return 0.0
    mean = sum(window) / len(window)
    var = sum((x - mean) ** 2 for x in window) / len(window)
    std = var ** 0.5
    return 0.0 if std == 0 else (value - mean) / std
