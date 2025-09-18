from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, tzinfo
from time import sleep
from typing import Iterator


_APP_TIMEZONE: tzinfo = timezone.utc


def init_clock(tz: tzinfo) -> None:
    global _APP_TIMEZONE
    _APP_TIMEZONE = tz


def get_timezone() -> tzinfo:
    return _APP_TIMEZONE


def utcnow() -> datetime:
    return datetime.now(_APP_TIMEZONE)


def sleep_seconds(seconds: float) -> None:
    sleep(max(0.0, seconds))


@contextmanager
def measure_time() -> Iterator[datetime]:
    start = utcnow()
    yield start
