"""Chart timeframe enumeration."""
from __future__ import annotations

from enum import Enum


class Timeframe(str, Enum):
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"

    @property
    def minutes(self) -> int:
        """Return the timeframe duration expressed in minutes."""

        value = self.value.lower()
        if not value[:-1].isdigit():
            raise ValueError(f"Timeframe {self.value} has an invalid duration")

        duration = int(value[:-1])
        unit = value[-1]
        unit_multipliers = {
            "m": 1,
            "h": 60,
            "d": 60 * 24,
            "w": 60 * 24 * 7,
        }

        try:
            multiplier = unit_multipliers[unit]
        except KeyError as exc:  # pragma: no cover - defensive branch
            raise ValueError(f"Timeframe {self.value} is not supported") from exc
        return duration * multiplier
