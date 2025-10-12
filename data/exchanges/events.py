from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Generic, Optional, TypeVar

from config.timezone import CURRENT_TIMEZONE


class ResyncReason(Enum):
    SEQUENCE_GAP = auto()
    SILENCE_TIMEOUT = auto()
    QUEUE_OVERFLOW = auto()
    CONNECTION_LOST = auto()


class StreamEventType(Enum):
    DATA = auto()
    SNAPSHOT = auto()
    RESYNC = auto()


T_cov = TypeVar("T_cov")


@dataclass(slots=True)
class StreamEvent(Generic[T_cov]):
    type: StreamEventType
    data: Optional[T_cov]
    timestamp: datetime
    reason: Optional[ResyncReason] = None
    details: Optional[str] = None

    @staticmethod
    def now(type: StreamEventType, data: Optional[T_cov] = None) -> "StreamEvent[T_cov]":
        return StreamEvent(
            type=type,
            data=data,
            timestamp=datetime.now(tz=CURRENT_TIMEZONE),
        )


