"""Thread-safe queue wrapper with heartbeat and timeout handling."""

from __future__ import annotations

import queue
import time
from threading import Event
from typing import Generic, Optional, TypeVar

from .exchange_logger import ExchangeLogger
from .resync_reason import ResyncReason
from .stream_event import StreamEvent
from .stream_event_type import StreamEventType

T = TypeVar("T")


class StreamBuffer(Generic[T]):
    """Thread-safe queue wrapper with heartbeat and timeout handling."""

    def __init__(
        self,
        name: str,
        logger: ExchangeLogger,
        silence_timeout: float,
        heartbeat_interval: Optional[float] = None,
        maxsize: int = 1024,
    ) -> None:
        self._name = name
        self._logger = logger
        self._queue: "queue.Queue[StreamEvent[T]]" = queue.Queue(maxsize)
        self._silence_timeout = silence_timeout
        self._heartbeat_interval = (
            heartbeat_interval if heartbeat_interval is not None else silence_timeout / 2
        )
        self._stop = Event()
        now = time.monotonic()
        self._last_data = now
        self._last_heartbeat = now

    def stopped(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        if not self._stop.is_set():
            self._stop.set()
            try:
                self._queue.put_nowait(StreamEvent.stop())
            except queue.Full:
                self._drain()
                self._queue.put_nowait(StreamEvent.stop())

    def push(self, event: StreamEvent[T]) -> None:
        if event.type in {StreamEventType.DATA, StreamEventType.SNAPSHOT}:
            self._last_data = time.monotonic()
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._logger.log_resync(
                ResyncReason.QUEUE_OVERFLOW,
                details=f"Стрим {self._name}: очередь переполнена",
            )
            self._drain()
            overflow = StreamEvent.resync(
                ResyncReason.QUEUE_OVERFLOW,
                details=f"Стрим {self._name}: очередь переполнена",
            )
            self._queue.put_nowait(overflow)

    def push_data(self, payload: T) -> None:
        self.push(StreamEvent.data_event(payload))

    def push_snapshot(self, payload: T) -> None:
        self.push(StreamEvent.snapshot_event(payload))

    def push_resync(self, reason: ResyncReason, details: str | None = None) -> None:
        self.push(StreamEvent.resync(reason, details))

    def next(self) -> StreamEvent[T]:
        while True:
            if self._stop.is_set():
                raise StopIteration
            try:
                event = self._queue.get(timeout=self._heartbeat_interval)
                if event.type == StreamEventType.STOP:
                    raise StopIteration
                return event
            except queue.Empty:
                now = time.monotonic()
                if now - self._last_data >= self._silence_timeout:
                    self._last_data = now
                    return StreamEvent.resync(
                        ResyncReason.SILENCE_TIMEOUT,
                        details=f"Стрим {self._name}: отсутствуют события {self._silence_timeout:.2f}с",
                    )
                if now - self._last_heartbeat >= self._heartbeat_interval:
                    self._last_heartbeat = now
                    return StreamEvent.heartbeat()

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


__all__ = ["StreamBuffer"]
