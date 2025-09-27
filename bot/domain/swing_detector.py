"""Utility class detecting swing highs/lows for trailing stops."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from bot import config


@dataclass(frozen=True)
class SwingDetectorSettings:
    """Configuration for swing detection heuristics."""

    window: int = config.TRAIL_SWING_WINDOW
    confirmation: int = config.TRAIL_SWING_CONFIRM


class SwingDetector:
    """Detects swing points used for trailing stop adjustments."""

    def __init__(self) -> None:
        self._settings = SwingDetectorSettings()

    def detect_swing_high(self, highs: Sequence[float]) -> float | None:
        return self._detect_pivot(highs, compare=max)

    def detect_swing_low(self, lows: Sequence[float]) -> float | None:
        return self._detect_pivot(lows, compare=min)

    def _detect_pivot(self, values: Sequence[float], compare: Callable[[Sequence[float]], float]) -> float | None:
        """Return the latest pivot value if it confirms as an extreme."""

        window = self._settings.window
        confirm = self._settings.confirmation
        required = window + confirm
        if len(values) < required or window <= 0:
            return None

        pivot_index = len(values) - confirm - 1
        start_index = max(0, pivot_index - window + 1)
        candidate_slice = values[start_index : pivot_index + confirm + 1]
        if not candidate_slice:
            return None

        pivot_value = values[pivot_index]
        if pivot_value == compare(candidate_slice):
            return float(pivot_value)
        return None
