"""Reasons that can trigger a resynchronisation of the stream."""

from enum import Enum


class ResyncReason(str, Enum):
    """Reasons that can trigger a resynchronisation of the stream."""

    SEQUENCE_GAP = "пропуск последовательности"
    QUEUE_OVERFLOW = "переполнение очереди"
    SILENCE_TIMEOUT = "таймаут тишины"
    CONNECTION_LOST = "потеря соединения"
    SNAPSHOT_REFRESH = "обновление снапшота"


__all__ = ["ResyncReason"]
