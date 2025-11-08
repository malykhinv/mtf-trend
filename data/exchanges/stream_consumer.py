from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Deque, Dict, Generic, Iterable, Optional, Tuple, TypeVar

from config.config import STREAM_METRICS_LOG_INTERVAL_MIN
from config.timezone import CURRENT_TIMEZONE
from data.logger import LogSink

from .events import ResyncReason, StreamEvent, StreamEventType
from .stream_buffer import StreamBuffer
from .stream_metrics import StreamMetrics

T = TypeVar("T")


SnapshotFactory = Optional[
    Callable[[], tuple[Optional[StreamEvent[T]], Iterable[StreamEvent[T]]]]
]


class StreamValidationError(RuntimeError):
    """Raised when a stream payload fails validation."""

    __slots__ = ("reason", "details", "resubscribe")

    def __init__(
        self,
        reason: ResyncReason,
        details: str,
        *,
        resubscribe: bool = True,
    ) -> None:
        super().__init__(details)
        self.reason = reason
        self.details = details
        self.resubscribe = resubscribe


@dataclass(slots=True, eq=False)
class StreamConsumer(Generic[T]):
    """In-memory handler used by shared sessions to deliver stream events."""

    stream: str
    symbol: str
    params: Tuple[str, ...]
    buffer: StreamBuffer[T]
    parser: Callable[[Dict[str, Any]], Iterable[StreamEvent[T]]]
    metrics: StreamMetrics
    log: LogSink
    silence_timeout_ms: int
    snapshot_factory: SnapshotFactory[T] = None
    _delay_threshold_ms: float = field(init=False, repr=False)
    _timeout_marks: Deque[datetime] = field(default_factory=deque, init=False, repr=False)
    _timeout_window: timedelta = field(init=False, repr=False)
    _timeout_limit: int = field(init=False, repr=False, default=3)
    _report_interval: timedelta = field(init=False, repr=False)
    _last_resync_reason: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._delay_threshold_ms = max(float(self.silence_timeout_ms) / 4.0, 250.0)
        window_seconds = max(float(self.silence_timeout_ms) / 1000.0 * 3.0, 15.0)
        self._timeout_window = timedelta(seconds=window_seconds)
        self._report_interval = timedelta(minutes=STREAM_METRICS_LOG_INTERVAL_MIN)

    def process(self, payload: Dict[str, Any]) -> bool:
        """Process incoming payload and push parsed events to the buffer."""

        try:
            events = list(self.parser(payload))
        except StreamValidationError as exc:
            needs_resubscribe = self.emit_resync(
                exc.reason,
                exc.details,
                enqueue_resubscribe=exc.resubscribe,
            )
            self.push_snapshot(reason=f"validation:{exc.reason.name}")
            return needs_resubscribe
        except Exception as exc:
            detail = f"исключение парсера: {exc}"
            return self.handle_exception(detail)
        reset_timeouts = False
        for event in events:
            self._record_latency(event.timestamp)
            self._push_event(event)
            if event.type in (StreamEventType.DATA, StreamEventType.SNAPSHOT):
                reset_timeouts = True
        if reset_timeouts:
            self._timeout_marks.clear()
        return False

    def handle_timeout(self) -> bool:
        details = f"{self.stream} silence timeout for {self.symbol}"
        resubscribe = self.emit_resync(
            ResyncReason.SILENCE_TIMEOUT,
            details,
            enqueue_resubscribe=True,
        )
        self.push_snapshot()
        return resubscribe

    def handle_ack(self, method: str, params: Tuple[str, ...], error: Any) -> bool:
        command = method.upper()
        if error is not None:
            details = f"command error for {params}: {error}"
            needs_resubscribe = self.emit_resync(
                ResyncReason.CONNECTION_LOST,
                details,
                enqueue_resubscribe=True,
            )
            self.push_snapshot(reason="ack-error")
            return needs_resubscribe
        now = datetime.now(tz=CURRENT_TIMEZONE)
        details = f"{command.lower()} confirmed for {' '.join(params)}"
        event = StreamEvent(StreamEventType.DATA, None, now, details=details)
        self.metrics.register_message(now)
        self._maybe_report(now)
        self._push_event(event)
        return False

    def handle_exception(self, details: str, *, resubscribe: bool = True) -> bool:
        now = datetime.now(tz=CURRENT_TIMEZONE)
        self.metrics.register_exception(now)
        needs_resubscribe = self.emit_resync(
            ResyncReason.CONNECTION_LOST,
            details,
            enqueue_resubscribe=resubscribe,
        )
        self.push_snapshot(reason="exception")
        return needs_resubscribe

    def emit_resync(
        self,
        reason: ResyncReason,
        details: str,
        *,
        enqueue_resubscribe: bool = True,
    ) -> bool:
        enqueue = enqueue_resubscribe
        attempt_suffix = ""
        now = datetime.now(tz=CURRENT_TIMEZONE)
        if reason == ResyncReason.SILENCE_TIMEOUT:
            enqueue = enqueue_resubscribe and self._register_timeout(now)
            attempts = len(self._timeout_marks)
            attempt_suffix = (
                f" [{min(attempts, self._timeout_limit)}/{self._timeout_limit} timeouts]"
            )
            if not enqueue:
                attempt_suffix += " (cooldown active)"
        else:
            if self._timeout_marks:
                self._timeout_marks.clear()
        detail_text = details or ""
        if attempt_suffix:
            if detail_text:
                detail_text = f"{detail_text}{attempt_suffix}"
            else:
                detail_text = attempt_suffix.strip()
        self.metrics.record_resync(reason)
        self.metrics.register_resync(now)
        recorded_reason = detail_text or reason.name
        self._last_resync_reason = f"{reason.name}:{recorded_reason}".strip(":")
        try:
            message = f"[{self.stream}:{self.symbol}] {detail_text} ({reason.name})"
            self.log(message)
        except Exception:  # pragma: no cover - logging failures ignored
            pass
        self._maybe_report(now)
        event = StreamEvent(
            StreamEventType.RESYNC,
            None,
            datetime.now(tz=CURRENT_TIMEZONE),
            reason=reason,
            details=detail_text,
        )
        self._push_event(event)
        return enqueue

    def _register_timeout(self, now: datetime) -> bool:
        cutoff = now - self._timeout_window
        while self._timeout_marks and self._timeout_marks[0] < cutoff:
            self._timeout_marks.popleft()
        self._timeout_marks.append(now)
        return len(self._timeout_marks) <= self._timeout_limit

    def push_snapshot(self, *, reason: str | None = None) -> None:
        if self.snapshot_factory is None:
            return
        try:
            snapshot_result = self.snapshot_factory()
        except StreamValidationError as exc:
            self.emit_resync(
                exc.reason,
                exc.details,
                enqueue_resubscribe=exc.resubscribe,
            )
            return
        if snapshot_result is None:
            return
        snapshot_event, replay_events = snapshot_result
        if snapshot_event is not None:
            self._push_event(snapshot_event)
        replay_list = list(replay_events)
        if replay_list:
            self.buffer.extend(replay_list)
        if reason is not None:
            replay_count = len(replay_list)
            try:
                self.log(
                    (
                        f"[{self.stream}:{self.symbol}] REST snapshot applied after {reason}; "
                        f"replayed_updates={replay_count}"
                    )
                )
            except Exception:  # pragma: no cover - defensive logging
                pass

    def get_last_resync_reason(self) -> str | None:
        return self._last_resync_reason

    def _record_latency(self, timestamp: datetime) -> None:
        now = datetime.now(tz=CURRENT_TIMEZONE)
        latency_ms = max((now - timestamp).total_seconds() * 1000.0, 0.0)
        self.metrics.record_latency(latency_ms, self._delay_threshold_ms)
        self.metrics.register_message(now)
        self._maybe_report(now)

    def _push_event(self, event: StreamEvent[T]) -> None:
        appended = self.buffer.append(event)
        if appended:
            return
        self.metrics.record_resync(ResyncReason.QUEUE_OVERFLOW)
        now = datetime.now(tz=CURRENT_TIMEZONE)
        self.metrics.register_resync(now)
        self._maybe_report(now)
        overflow = StreamEvent(
            StreamEventType.RESYNC,
            None,
            datetime.now(tz=CURRENT_TIMEZONE),
            reason=ResyncReason.QUEUE_OVERFLOW,
            details=f"buffer overflow on {self.stream} stream",
        )
        self.buffer.append(overflow)

    def _maybe_report(self, now: datetime) -> None:
        summary = self.metrics.consume_report(now, self._report_interval)
        if summary is None:
            return
        messages, resyncs, exceptions = summary
        report = (
            f"[{self.stream}:{self.symbol}] Сводка за "
            f"{self._report_interval.total_seconds() / 60:.0f}м: "
            f"сообщений={messages}, ресинки={resyncs}, исключения={exceptions}"
        )
        try:
            self.log(report)
        except Exception:  # pragma: no cover - logging failures ignored
            pass


__all__ = [
    "StreamConsumer",
    "StreamValidationError",
    "SnapshotFactory",
]
