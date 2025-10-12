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
    def __init__(
        self,
        name: str,
        logger: ExchangeLogger,
        silence_timeout: float,
        heartbeat_interval: Optional[float] = None,
        maxsize: int | None = None,
        drop_oldest_on_overflow: bool = False,
        restart_grace_period: Optional[float] = None,
    ) -> None:
        self._name = name
        self._logger = logger
        queue_size = 1024 if maxsize is None else int(maxsize)
        self._queue: "queue.Queue[StreamEvent[T]]" = queue.Queue(queue_size)
        self._silence_timeout = silence_timeout
        self._heartbeat_interval = (
            heartbeat_interval if heartbeat_interval is not None else silence_timeout / 2
        )
        self._drop_oldest_on_overflow = drop_oldest_on_overflow
        self._stop = Event()
        self._restart_event = Event()
        now = time.monotonic()
        self._last_data = now
        self._last_heartbeat = now
        self._restart_grace_period = (
            max(0.0, restart_grace_period)
            if restart_grace_period is not None
            else silence_timeout
        )
        self._last_restart_requested_at: float | None = None
        self._last_restart_reason: ResyncReason | None = None

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

    def request_restart(
        self,
        reason: ResyncReason | None = None,
        *,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force:
            if self._restart_event.is_set():
                return
            if (
                self._last_restart_requested_at is not None
                and now - self._last_restart_requested_at < self._restart_grace_period
            ):
                return
        self._restart_event.set()
        self._last_restart_requested_at = now
        self._last_restart_reason = reason

    def consume_restart_request(self) -> ResyncReason | None:
        if self._restart_event.is_set():
            self._restart_event.clear()
            reason = self._last_restart_reason
            self._last_restart_reason = None
            return reason
        return None

    def restart_requested(self) -> bool:
        return self._restart_event.is_set()

    def restart_grace_period(self) -> float:
        return self._restart_grace_period

    def last_restart_request_age(self) -> float | None:
        if self._last_restart_requested_at is None:
            return None
        return time.monotonic() - self._last_restart_requested_at

    def push(self, event: StreamEvent[T]) -> None:
        if event.type in {StreamEventType.DATA, StreamEventType.SNAPSHOT}:
            self._last_data = time.monotonic()
            self._last_restart_requested_at = None
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            if self._drop_oldest_on_overflow:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                else:
                    try:
                        self._queue.put_nowait(event)
                        return
                    except queue.Full:
                        pass
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
                    self.request_restart(ResyncReason.SILENCE_TIMEOUT)
                    self._last_heartbeat = now
                    return StreamEvent.heartbeat()
                if now - self._last_heartbeat >= self._heartbeat_interval:
                    self._last_heartbeat = now
                    return StreamEvent.heartbeat()

    def drain_pending(self) -> list[StreamEvent[T]]:
        events: list[StreamEvent[T]] = []
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            if event.type == StreamEventType.STOP:
                self._stop.set()
                break
            events.append(event)
        return events

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


__all__ = ["StreamBuffer"]
