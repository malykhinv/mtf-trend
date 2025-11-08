from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .events import ResyncReason


@dataclass(slots=True)
class StreamMetrics:
    name: str
    latency_max_ms: float = 0.0
    latency_last_ms: float = 0.0
    delayed_messages: int = 0
    sequence_gaps: int = 0
    silence_timeouts: int = 0
    reconnects: int = 0
    queue_overflows: int = 0
    messages: int = 0
    last_report_at: datetime | None = None
    messages_since_report: int = 0
    resyncs_since_report: int = 0
    exceptions_since_report: int = 0

    def record_latency(self, latency_ms: float, threshold_ms: float) -> None:
        self.latency_last_ms = latency_ms
        if latency_ms > self.latency_max_ms:
            self.latency_max_ms = latency_ms
        if latency_ms > threshold_ms:
            self.delayed_messages += 1
        self.messages += 1

    def record_resync(self, reason: ResyncReason) -> None:
        if reason == ResyncReason.SEQUENCE_GAP:
            self.sequence_gaps += 1
        elif reason == ResyncReason.SILENCE_TIMEOUT:
            self.silence_timeouts += 1
        elif reason == ResyncReason.CONNECTION_LOST:
            self.reconnects += 1
        elif reason == ResyncReason.QUEUE_OVERFLOW:
            self.queue_overflows += 1

    def register_message(self, now: datetime) -> None:
        self.messages_since_report += 1
        if self.last_report_at is None:
            self.last_report_at = now

    def register_resync(self, now: datetime) -> None:
        self.resyncs_since_report += 1
        if self.last_report_at is None:
            self.last_report_at = now

    def register_exception(self, now: datetime) -> None:
        self.exceptions_since_report += 1
        if self.last_report_at is None:
            self.last_report_at = now

    def consume_report(
        self,
        now: datetime,
        interval: timedelta,
    ) -> tuple[int, int, int] | None:
        if self.last_report_at is None:
            self.last_report_at = now
            return None
        if now - self.last_report_at < interval:
            return None
        summary = (
            self.messages_since_report,
            self.resyncs_since_report,
            self.exceptions_since_report,
        )
        self.last_report_at = now
        self.messages_since_report = 0
        self.resyncs_since_report = 0
        self.exceptions_since_report = 0
        return summary


__all__ = ["StreamMetrics"]
