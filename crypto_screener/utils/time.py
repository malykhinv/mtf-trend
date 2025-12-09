from datetime import datetime, timezone


def ensure_utc(dt: datetime) -> datetime:
    """Normalize datetime to UTC to avoid timezone drift in comparisons."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)
