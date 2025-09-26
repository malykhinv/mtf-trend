from __future__ import annotations

from datetime import datetime, timezone, tzinfo


_APP_TIMEZONE: tzinfo = timezone.utc


def init_clock(tz: tzinfo) -> None:
    global _APP_TIMEZONE
    _APP_TIMEZONE = tz


def get_timezone() -> tzinfo:
    return _APP_TIMEZONE


def utcnow() -> datetime:
    return datetime.now(_APP_TIMEZONE)
