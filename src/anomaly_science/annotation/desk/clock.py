"""Clock helpers for append-only annotation artifacts."""

from __future__ import annotations

import time
from datetime import UTC, datetime


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def utc_iso_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat().replace("+00:00", "Z")
