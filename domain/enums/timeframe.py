"""Модуль проекта."""

from __future__ import annotations

from enum import Enum
import re


_TIMEFRAME_PATTERN = re.compile(r"^(\d+)([mhdw])$")
_SECONDS_PER_UNIT = {
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
}


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"

    def to_milliseconds(self) -> int:
        """Возвращает длительность таймфрейма в миллисекундах."""
        match = _TIMEFRAME_PATTERN.fullmatch(self.value)
        if match is None:
            raise ValueError(f"Неизвестный формат таймфрейма: {self.value}")

        amount = int(match.group(1))
        unit = match.group(2)
        seconds = amount * _SECONDS_PER_UNIT[unit]
        return seconds * 1000
