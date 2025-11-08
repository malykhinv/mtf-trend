from __future__ import annotations

from typing import Callable, Generic, Iterator, Optional, TypeVar

from .events import StreamEvent
from .stream_buffer import StreamBuffer

T = TypeVar("T")


class StreamSubscription(Generic[T]):
    """Subscription wrapper that exposes stream events to the application."""

    __slots__ = ("events", "_buffer", "_stop", "_stopped")

    def __init__(
            self,
            events: Iterator[StreamEvent[T]],
            buffer: StreamBuffer[T],
            stop_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        self.events = events
        self._buffer = buffer
        self._stop = stop_callback
        self._stopped = False

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._stop is not None:
            self._stop()

    def assign_stop(self, stop_callback: Optional[Callable[[], None]]) -> None:
        """Assign or replace the stop callback used by the subscription."""

        self._stop = stop_callback


class StreamLimitError(RuntimeError):
    """Raised when stream scheduling exceeds calculated limits."""


__all__ = ["StreamSubscription", "StreamLimitError"]
