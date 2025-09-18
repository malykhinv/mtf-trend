from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from time import sleep
from typing import Iterator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sleep_seconds(seconds: float) -> None:
    sleep(max(0.0, seconds))


@contextmanager
def measure_time() -> Iterator[datetime]:
    start = utcnow()
    yield start
