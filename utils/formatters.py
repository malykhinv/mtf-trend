"""Вспомогательные функции форматирования даты и времени без конвертации таймзон."""

from __future__ import annotations

from datetime import datetime


def timestamp_ms_to_datetime(timestamp_ms: int | float) -> datetime:
    """Преобразует Unix timestamp в миллисекундах в datetime без дополнительных преобразований."""
    return datetime.fromtimestamp(float(timestamp_ms) / 1000.0)



def datetime_to_timestamp_ms(value: datetime) -> int:
    """Возвращает Unix timestamp в миллисекундах без дополнительных преобразований."""
    return int(value.timestamp() * 1000)


def format_datetime_human(value: datetime) -> str:
    """Форматирует дату и время в человекочитаемый вид без таймзоны."""
    return value.strftime("%d.%m.%Y %H:%M:%S")
