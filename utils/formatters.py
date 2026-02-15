"""Вспомогательные функции форматирования даты и времени."""

from __future__ import annotations

from datetime import datetime


def format_datetime_human(value: datetime) -> str:
    """Форматирует дату и время в человекочитаемый вид без таймзоны."""
    return value.strftime("%d.%m.%Y %H:%M:%S")
