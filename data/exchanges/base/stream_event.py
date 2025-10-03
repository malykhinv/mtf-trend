"""Wrapper describing data, heartbeat and resync events coming from streams."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Generic, Optional, TypeVar

from .resync_reason import ResyncReason
from .stream_event_type import StreamEventType

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StreamEvent(Generic[T]):
    """Wrapper describing data, heartbeat and resync events coming from streams."""

    type: StreamEventType
    data: Optional[T] = None
    reason: Optional[ResyncReason] = None
    details: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

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
