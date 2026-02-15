"""Вспомогательные функции форматирования даты и времени."""

from __future__ import annotations

from datetime import UTC, datetime


def format_datetime_human(timestamp_ms: int) -> str:
    """Форматирует unix timestamp в миллисекундах в человекочитаемый UTC вид."""
    if not isinstance(timestamp_ms, int):
        msg = "Timestamp must be int in unix milliseconds."
        raise TypeError(msg)
    if timestamp_ms < 0:
        msg = "Timestamp must be non-negative."
        raise ValueError(msg)
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%d.%m.%Y %H:%M:%S")
