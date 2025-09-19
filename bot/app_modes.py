from __future__ import annotations

from enum import Enum


class AppMode(str, Enum):
    BACKTEST = "backtest"
    LIVE = "live"

    @classmethod
    def parse(cls, raw: object) -> "AppMode":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, str):
            candidate = raw.strip().lower()
            if candidate:
                for mode in cls:
                    if mode.value == candidate:
                        return mode
        expected = ", ".join(mode.value for mode in cls)
        raise ValueError(f"Unsupported mode '{raw}'. Expected one of: {expected}")
