from __future__ import annotations

import threading
from typing import Callable, Generic, Optional, TypeVar

from data.exchanges import StreamEvent, StreamEventType, StreamSubscription

T = TypeVar("T")


class StreamPump(Generic[T]):
    """Background worker that forwards stream events to a callback."""

    def __init__(
        self,
        subscription: StreamSubscription[T],
        callback: Callable[[StreamEvent[T]], None],
        *,
        name: Optional[str] = None,
    ) -> None:
        self._subscription = subscription
        self._callback = callback
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=name,
            daemon=True,
        )

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        # Ensure the iterator unblocks so the thread can exit promptly.
        try:
            self._subscription._buffer.stop()  # type: ignore[attr-defined]
        except Exception:
            pass

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread.is_alive():
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        try:
            for event in self._subscription.events:
                self._callback(event)
                if self._stop_event.is_set() or event.type is StreamEventType.STOP:
                    break
        finally:
            self._stop_event.set()


__all__ = ["StreamPump"]
