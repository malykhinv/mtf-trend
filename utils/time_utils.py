from datetime import datetime, timezone
from config.constants import TIMEZONE


def to_local_dt(ms_timestamp: int) -> datetime:
    """
    Преобразует миллисекундный unix timestamp в datetime с учётом локальной TIMEZONE.

    Args:
        ms_timestamp (int): Unix timestamp в миллисекундах.

    Returns:
        datetime: локализованный datetime.
    """
    return datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc).astimezone(TIMEZONE)
