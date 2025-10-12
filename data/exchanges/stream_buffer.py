from __future__ import annotations

from collections import deque
from typing import Deque, Generic, Iterable, List, Optional, TypeVar

from .events import StreamEvent

T = TypeVar("T")


class StreamBuffer(Generic[T]):
    """A tiny FIFO buffer used by stream subscriptions."""

    __slots__ = ("_maxsize", "_queue")

    def __init__(self, maxsize: Optional[int] = None) -> None:
        self._maxsize = maxsize
        self._queue: Deque[StreamEvent[T]] = deque()

    def __len__(self) -> int:
        return len(self._queue)

    def append(self, event: StreamEvent[T]) -> bool:
        if self._maxsize is not None and len(self._queue) >= self._maxsize:
            return False
        self._queue.append(event)
        return True

    def extend(self, events: Iterable[StreamEvent[T]]) -> None:
        for event in events:
            appended = self.append(event)
            if not appended:
                break

    def drain_pending(self) -> List[StreamEvent[T]]:
        if not self._queue:
            return []
        drained = list(self._queue)
        self._queue.clear()
        return drained


