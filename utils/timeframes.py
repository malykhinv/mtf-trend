from __future__ import annotations

from datetime import timedelta


_TIMEFRAME_TO_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


def to_timedelta(tf: str) -> timedelta:
    minutes = _TIMEFRAME_TO_MINUTES[tf]
    return timedelta(minutes=minutes)

