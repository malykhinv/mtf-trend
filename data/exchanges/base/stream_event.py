from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, Optional, TypeVar

from utils.timez import get_current_time
from .resync_reason import ResyncReason
from .stream_event_type import StreamEventType

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StreamEvent(Generic[T]):
    type: StreamEventType
    data: Optional[T] = None
    reason: Optional[ResyncReason] = None
    details: Optional[str] = None
    timestamp: datetime = field(default_factory=get_current_time)

    @classmethod
    def data_event(cls, payload: T) -> "StreamEvent[T]":
        return cls(type=StreamEventType.DATA, data=payload)

    @classmethod
    def snapshot_event(cls, payload: T) -> "StreamEvent[T]":
        return cls(type=StreamEventType.SNAPSHOT, data=payload)

    @classmethod
    def heartbeat(cls) -> "StreamEvent[T]":
        return cls(type=StreamEventType.HEARTBEAT)

    @classmethod
    def resync(cls, reason: ResyncReason, details: str | None = None) -> "StreamEvent[T]":
        return cls(type=StreamEventType.RESYNC, reason=reason, details=details)

    @classmethod
    def stop(cls) -> "StreamEvent[T]":
        return cls(type=StreamEventType.STOP)


__all__ = ["StreamEvent"]
