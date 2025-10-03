from enum import Enum


class StreamEventType(str, Enum):
    DATA = "data"
    SNAPSHOT = "snapshot"
    HEARTBEAT = "heartbeat"
    RESYNC = "resync"
    STOP = "stop"


__all__ = ["StreamEventType"]
