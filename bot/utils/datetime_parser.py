"""Datetime parsing helpers."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def parse_iso_datetime(value: str, *, timezone: ZoneInfo) -> datetime:
    """Parse ISO 8601 datetime strings and normalize to ``timezone``.

    ``value`` may omit timezone information or use ``Z`` suffix for UTC.
    """

    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)
