"""Type of event yielded by a streaming iterator."""

from enum import Enum


class StreamEventType(str, Enum):
    """Type of event yielded by a streaming iterator."""

    DATA = "data"
    SNAPSHOT = "snapshot"
    HEARTBEAT = "heartbeat"
    RESYNC = "resync"
    STOP = "stop"


__all__ = ["StreamEventType"]
