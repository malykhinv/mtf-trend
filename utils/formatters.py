"""Вспомогательные функции для часовых поясов и форматирования даты и времени."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_STORAGE_TIMEZONE = timezone.utc


def resolve_timezone(timezone_name: str) -> ZoneInfo:
    """Преобразует имя часового пояса в объект часового пояса."""
    return ZoneInfo(timezone_name)


def utc_ms_to_local_datetime(timestamp_ms: int | float, timezone_name: str) -> datetime:
    """Преобразует UTC-метку времени в миллисекундах в локальную дату и время."""
    tz = resolve_timezone(timezone_name)
    utc_dt = datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc)
    return utc_dt.astimezone(tz)


def datetime_to_timezone(value: datetime, timezone_name: str) -> datetime:
    """Конвертирует объект даты и времени с часовым поясом или без него в указанный часовой пояс; значение без пояса трактуется как целевой пояс."""
    tz = resolve_timezone(timezone_name)
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def datetime_to_utc(value: datetime, source_timezone_name: str | None = None) -> datetime:
    """Преобразует дату и время в UTC."""
    if value.tzinfo is None:
        if source_timezone_name is None:
            aware = value.replace(tzinfo=timezone.utc)
        else:
            aware = value.replace(tzinfo=resolve_timezone(source_timezone_name))
    else:
        aware = value
    return aware.astimezone(timezone.utc)


def datetime_to_utc_ms(value: datetime, source_timezone_name: str | None = None) -> int:
    """Преобразует дату и время в UTC-метку времени в миллисекундах."""
    utc_dt = datetime_to_utc(value, source_timezone_name=source_timezone_name)
    return int(utc_dt.timestamp() * 1000)


def format_datetime_human(value: datetime) -> str:
    """Форматирует дату и время в человекочитаемый вид с часовым поясом."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime("%d.%m.%Y %H:%M:%S %Z")
