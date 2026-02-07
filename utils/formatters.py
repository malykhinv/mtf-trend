"""Timezone and datetime formatting helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_STORAGE_TIMEZONE = timezone.utc


def resolve_timezone(timezone_name: str) -> ZoneInfo:
    """Resolve IANA timezone name into ZoneInfo instance."""
    return ZoneInfo(timezone_name)


def utc_ms_to_local_datetime(timestamp_ms: int | float, timezone_name: str) -> datetime:
    """Convert UTC timestamp in milliseconds to timezone-aware datetime in provided TZ."""
    tz = resolve_timezone(timezone_name)
    utc_dt = datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc)
    return utc_dt.astimezone(tz)


def datetime_to_timezone(value: datetime, timezone_name: str) -> datetime:
    """Convert aware/naive datetime to provided TZ. Naive datetimes are treated as target TZ."""
    tz = resolve_timezone(timezone_name)
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def datetime_to_utc(value: datetime, source_timezone_name: str | None = None) -> datetime:
    """Convert aware/naive datetime to UTC; naive is interpreted in source_timezone_name or UTC."""
    if value.tzinfo is None:
        if source_timezone_name is None:
            aware = value.replace(tzinfo=timezone.utc)
        else:
            aware = value.replace(tzinfo=resolve_timezone(source_timezone_name))
    else:
        aware = value
    return aware.astimezone(timezone.utc)


def datetime_to_utc_ms(value: datetime, source_timezone_name: str | None = None) -> int:
    """Convert aware/naive datetime to UTC timestamp in milliseconds."""
    utc_dt = datetime_to_utc(value, source_timezone_name=source_timezone_name)
    return int(utc_dt.timestamp() * 1000)
