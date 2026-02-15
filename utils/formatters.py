"""Вспомогательные функции форматирования даты и времени без конвертации таймзон."""

from __future__ import annotations

from datetime import datetime

DEFAULT_STORAGE_TIMEZONE = None


def utc_ms_to_local_datetime(timestamp_ms: int | float, timezone_name: str) -> datetime:
    """Преобразует метку времени в миллисекундах в datetime без нормализации часового пояса."""
    _ = timezone_name
    return datetime.fromtimestamp(float(timestamp_ms) / 1000.0)


def datetime_to_timezone(value: datetime, timezone_name: str) -> datetime:
    """Возвращает исходный datetime без конвертации между часовыми поясами."""
    _ = timezone_name
    return value


def datetime_to_utc(value: datetime, source_timezone_name: str | None = None) -> datetime:
    """Возвращает исходный datetime без нормализации в UTC."""
    _ = source_timezone_name
    return value


def datetime_to_utc_ms(value: datetime, source_timezone_name: str | None = None) -> int:
    """Возвращает Unix timestamp в миллисекундах без преобразований таймзоны."""
    _ = source_timezone_name
    return int(value.timestamp() * 1000)


def format_datetime_human(value: datetime) -> str:
    """Форматирует дату и время в человекочитаемый вид без таймзоны."""
    return value.strftime("%d.%m.%Y %H:%M:%S")
