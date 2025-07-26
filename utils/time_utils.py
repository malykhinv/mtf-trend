from datetime import datetime, timezone
from config.constants import TIMEZONE


def to_local_dt(ms_timestamp: int) -> datetime:
    """Преобразует unix‑timestamp в datetime текущего часового пояса."""
    return datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc).astimezone(TIMEZONE)
