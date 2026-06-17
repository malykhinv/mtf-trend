"""Clock helpers for anomaly live2."""

from __future__ import annotations

import time
from datetime import UTC, datetime


def utc_now_ms() -> int:
    """Return current UTC wall-clock time in milliseconds."""

    return int(time.time() * 1000)


def monotonic_ms() -> int:
    """Return monotonic time in milliseconds for latency/deadline measurement."""

    return time.monotonic_ns() // 1_000_000


def utc_now_iso() -> str:
    """Return compact ISO-8601 UTC timestamp."""

    return datetime.now(UTC).isoformat(timespec="milliseconds")
