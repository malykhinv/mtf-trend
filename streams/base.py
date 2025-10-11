from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Generic, Optional, TypeVar

from data.exchanges import ResyncReason, StreamBuffer, StreamEvent
from utils.metrics import METRICS


T = TypeVar("T")


@dataclass(frozen=True)
class AlarmThresholds:
    """Defines queue backlog thresholds for warning and critical states."""

    warning: int
    critical: int


class FallbackMode(str, Enum):
    """Fallback strategy when the pipeline becomes overloaded."""

    AGGREGATE = "aggregate"
    SKIP = "skip"


@dataclass(frozen=True)
class PipelineHealth:
    """Snapshot of the pipeline health and mitigation state."""

    backlog: int
    capacity: int
    is_degraded: bool
    fallback_mode: FallbackMode
    aggregated_events: int
    skipped_events: int
    degraded_since: Optional[float]


class StreamPipeline(Generic[T]):
    """Wraps a :class:`StreamBuffer` with overload mitigation helpers."""

    def __init__(
        self,
        *,
        name: str,
        buffer: StreamBuffer[T],
        thresholds: AlarmThresholds,
        fallback_mode: FallbackMode,
        aggregator: Optional[Callable[[T, T], T]] = None,
        degradation_hold_s: float = 30.0,
        chronic_error_threshold: int = 3,
        chronic_error_window_s: float = 30.0,
        on_chronic_error: Optional[Callable[[ResyncReason, str], None]] = None,
        metrics_stream: Optional[str] = None,
        metrics_symbol: Optional[str] = None,
    ) -> None:
        self._name = name
        self._buffer = buffer
        self._thresholds = thresholds
        self._fallback_mode = fallback_mode
        self._aggregator = aggregator
        self._pending_aggregate: Optional[T] = None
        self._aggregated_events = 0
        self._skipped_events = 0
        self._degraded_since: Optional[float] = None
        self._degradation_hold_s = max(float(degradation_hold_s), 0.0)
        self._error_timestamps: list[float] = []
        self._chronic_error_threshold = max(1, int(chronic_error_threshold))
        self._chronic_error_window_s = max(float(chronic_error_window_s), 0.0)
        self._on_chronic_error = on_chronic_error
        self._metrics_stream = metrics_stream or name
        self._metrics_symbol = metrics_symbol

    @property
    def buffer(self) -> StreamBuffer[T]:
        return self._buffer

    @property
    def fallback_mode(self) -> FallbackMode:
        return self._fallback_mode

    @property
    def degradation_hold_s(self) -> float:
        return self._degradation_hold_s

    @property
    def is_degraded(self) -> bool:
        if self._degraded_since is None:
            return False
        now = time.monotonic()
        if now - self._degraded_since >= self._degradation_hold_s:
            return False
        return True

    def reset(self) -> None:
        self._pending_aggregate = None
        self._aggregated_events = 0
        self._skipped_events = 0
        self._degraded_since = None
        self._error_timestamps.clear()

    def push_data(self, payload: T) -> None:
        backlog = self._buffer.backlog()
        if backlog >= self._thresholds.critical:
            self._enter_degraded_state()
            if self._fallback_mode is FallbackMode.SKIP:
                self._skipped_events += 1
                self._observe_backlog(backlog)
                return
            if self._fallback_mode is FallbackMode.AGGREGATE and self._aggregator is not None:
                self._aggregate(payload)
                self._observe_backlog(backlog)
                return
        elif backlog >= self._thresholds.warning:
            self._enter_degraded_state()
            if self._fallback_mode is FallbackMode.AGGREGATE and self._aggregator is not None:
                self._aggregate(payload)
                self._observe_backlog(backlog)
                return
        else:
            self._flush_pending()
            self._maybe_expire_degradation()
        self._buffer.push_data(payload)
        self._observe_backlog()
        self._record_flow()

    def push_snapshot(self, payload: T) -> None:
        self._flush_pending()
        self._maybe_expire_degradation()
        self._buffer.push_snapshot(payload)
        self._observe_backlog()
        self._record_flow()

    def push_resync(self, reason: ResyncReason, details: str | None = None) -> None:
        self._flush_pending()
        self._enter_degraded_state()
        self._buffer.push_resync(reason, details)
        self._record_error(reason, details)
        METRICS.record_resync_trigger(self._metrics_stream, self._metrics_symbol, reason.value)
        self._observe_backlog()

    def next_event(self) -> StreamEvent[T]:
        event = self._buffer.next()
        if event.type is not None:
            backlog = self._buffer.backlog()
            if backlog < self._thresholds.warning:
                self._maybe_expire_degradation()
            self._observe_backlog(backlog)
        return event

    def health_snapshot(self) -> PipelineHealth:
        return PipelineHealth(
            backlog=self._buffer.backlog(),
            capacity=self._buffer.capacity,
            is_degraded=self.is_degraded,
            fallback_mode=self._fallback_mode,
            aggregated_events=self._aggregated_events,
            skipped_events=self._skipped_events,
            degraded_since=self._degraded_since,
        )

    def _aggregate(self, payload: T) -> None:
        if self._pending_aggregate is None:
            self._pending_aggregate = payload
        else:
            if self._aggregator is None:
                self._pending_aggregate = payload
            else:
                self._pending_aggregate = self._aggregator(self._pending_aggregate, payload)
        self._aggregated_events += 1

    def _flush_pending(self) -> None:
        if self._pending_aggregate is None:
            return
        aggregated = self._pending_aggregate
        self._pending_aggregate = None
        self._buffer.push_data(aggregated)

    def _enter_degraded_state(self) -> None:
        self._degraded_since = time.monotonic()

    def _maybe_expire_degradation(self) -> None:
        if self._degraded_since is None:
            return
        now = time.monotonic()
        if now - self._degraded_since >= self._degradation_hold_s:
            self._degraded_since = None

    def _record_error(self, reason: ResyncReason, details: str | None) -> None:
        if self._on_chronic_error is None:
            return
        now = time.monotonic()
        self._error_timestamps.append(now)
        window = self._chronic_error_window_s
        if window > 0.0:
            cutoff = now - window
            self._error_timestamps = [ts for ts in self._error_timestamps if ts >= cutoff]
        if len(self._error_timestamps) >= self._chronic_error_threshold:
            try:
                self._on_chronic_error(reason, details or "chronic stream error")
            finally:
                self._error_timestamps.clear()

    def _observe_backlog(self, backlog: Optional[int] = None) -> None:
        metric_backlog = backlog if backlog is not None else self._buffer.backlog()
        METRICS.observe_queue_depth(
            self._metrics_stream,
            self._metrics_symbol,
            metric_backlog,
            self._buffer.capacity,
            self.is_degraded,
        )

    def _record_flow(self) -> None:
        symbol = self._metrics_symbol or "unknown"
        METRICS.record_flow(self._metrics_stream, symbol)

*** End of File
