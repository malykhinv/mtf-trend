from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ResyncReason(str, Enum):
    SEQUENCE_GAP = "пропуск последовательности"
    CONNECTION_LOSS = "потеря соединения"
    SILENCE = "тишина канала"
    BACKPRESSURE = "переполнение очереди"


@dataclass(frozen=True)
class FeedStatus:
    has_sequence_gap: bool = False
    has_connection_loss: bool = False
    has_silence_timeout: bool = False
    has_queue_overflow: bool = False

    def resolve_reason(self) -> Optional[ResyncReason]:
        if self.has_sequence_gap:
            return ResyncReason.SEQUENCE_GAP
        if self.has_connection_loss:
            return ResyncReason.CONNECTION_LOSS
        if self.has_silence_timeout:
            return ResyncReason.SILENCE
        if self.has_queue_overflow:
            return ResyncReason.BACKPRESSURE
        return None


__all__ = ["ResyncReason", "FeedStatus"]
