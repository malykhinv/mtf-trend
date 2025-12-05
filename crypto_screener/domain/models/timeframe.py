from __future__ import annotations

from enum import Enum


class Timeframe(Enum):
    M1 = ("1m", 1)
    M5 = ("5m", 5)
    M15 = ("15m", 15)
    M30 = ("30m", 30)
    H1 = ("1h", 60)
    H4 = ("4h", 240)

    @property
    def tf(self) -> str:
        return self.value[0]

    @property
    def minutes(self) -> int:
        return self.value[1]

    @classmethod
    def from_tf(cls, tf: str) -> "Timeframe":
        for timeframe in cls:
            if timeframe.tf == tf:
                return timeframe
        raise ValueError(f"Неизвестный таймфрейм: {tf}")
