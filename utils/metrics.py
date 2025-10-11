from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Deque, Dict, Iterable, Mapping, Optional, Tuple

try:  # pragma: no cover - optional dependency
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server
except Exception:  # pragma: no cover - optional dependency
    CollectorRegistry = None  # type: ignore[assignment]
    Counter = None  # type: ignore[assignment]
    Gauge = None  # type: ignore[assignment]
    Histogram = None  # type: ignore[assignment]

    def start_http_server(*_args: object, **_kwargs: object) -> None:  # type: ignore[return-type]
        return None


JsonSink = Callable[[str], None]


@dataclass(slots=True)
class _SummaryBucket:
    count: int = 0
    total: float = 0.0
    maximum: float = 0.0

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        if value > self.maximum:
            self.maximum = value

    def as_dict(self) -> Dict[str, float]:
        average = self.total / self.count if self.count else 0.0
        return {"count": float(self.count), "avg": average, "max": self.maximum}


@dataclass(slots=True)
class _QueueSnapshot:
    max_backlog: int = 0
    max_ratio: float = 0.0

    def observe(self, backlog: int, capacity: int) -> None:
        if backlog > self.max_backlog:
            self.max_backlog = backlog
        ratio = backlog / capacity if capacity > 0 else 0.0
        if ratio > self.max_ratio:
            self.max_ratio = ratio

    def as_dict(self) -> Dict[str, float]:
        return {"max_backlog": float(self.max_backlog), "max_ratio": self.max_ratio}


@dataclass(slots=True)
class MetricsThresholds:
    resubscribe_ratio: float
    resubscribe_window_s: float
    silence_timeout_s: float
    backpressure_ratio: float


class MetricsService:
    """Centralised metrics and alerting helper for streaming components."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._enabled = False
        self._json_sink: Optional[JsonSink] = None
        self._thresholds = MetricsThresholds(
            resubscribe_ratio=1.0,
            resubscribe_window_s=300.0,
            silence_timeout_s=300.0,
            backpressure_ratio=0.8,
        )
        self._registry: Optional[CollectorRegistry] = None
        self._gauges: Dict[str, Gauge] = {}
        self._counters: Dict[str, Counter] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._prometheus_started = False
        self._command_history: Dict[str, Deque[Tuple[float, str]]] = defaultdict(deque)
        self._silence_state: Dict[Tuple[str, str], float] = {}
        self._daily_commands: Dict[Tuple[str, str], _SummaryBucket] = defaultdict(_SummaryBucket)
        self._daily_resync: Dict[Tuple[str, str], _SummaryBucket] = defaultdict(_SummaryBucket)
        self._daily_migrations: Dict[str, int] = defaultdict(int)
        self._daily_queue: Dict[Tuple[str, str], _QueueSnapshot] = defaultdict(_QueueSnapshot)
        self._daily_report_hour = 0
        self._daily_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def configure(
        self,
        *,
        enabled: bool,
        prometheus_port: Optional[int],
        json_sink: Optional[JsonSink],
        thresholds: MetricsThresholds,
        daily_report_hour: int,
    ) -> None:
        with self._lock:
            self._enabled = enabled
            self._json_sink = json_sink
            self._thresholds = thresholds
            self._daily_report_hour = max(0, min(int(daily_report_hour), 23))
            self._stop_event.clear()
        if not enabled:
            return
        if prometheus_port is not None:
            self._ensure_prometheus(prometheus_port)
        self._ensure_daily_reporter()

    # ------------------------------------------------------------------
    # Metrics recording helpers
    # ------------------------------------------------------------------
    def set_active_symbols(self, stream: str, worker: str, count: int) -> None:
        if not self._enabled:
            return
        payload = {
            "timestamp": self._utc_timestamp(),
            "metric": "active_symbols",
            "stream": stream,
            "worker": worker,
            "count": count,
        }
        with self._lock:
            gauge = self._gauge(
                "stream_active_symbols",
                "Number of active symbols per worker",
                ("stream", "worker"),
            )
            if gauge is not None:
                gauge.labels(stream=stream, worker=worker).set(count)
        self._emit_json(payload)

    def observe_command_latency(
        self,
        stream: str,
        tags: Mapping[str, str],
        latency: float,
        attempts: float,
    ) -> None:
        if not self._enabled:
            return
        command = tags.get("type") or tags.get("command") or "command"
        success = tags.get("success", "1") == "1"
        payload = {
            "timestamp": self._utc_timestamp(),
            "metric": "command_latency",
            "stream": stream,
            "command": command,
            "latency": latency,
            "attempts": attempts,
            "success": success,
        }
        with self._lock:
            histogram = self._histogram(
                "stream_command_latency_seconds",
                "Latency of websocket commands",
                ("stream", "command", "success"),
            )
            if histogram is not None:
                histogram.labels(
                    stream=stream,
                    command=command,
                    success="1" if success else "0",
                ).observe(latency)
            self._daily_commands[(stream, command)].observe(latency)
            history = self._command_history[stream]
            now = time.monotonic()
            history.append((now, command))
            self._prune_history(history, now)
            self._check_resubscribe_ratio(stream, history)
        self._emit_json(payload)

    def observe_queue_depth(
        self,
        stream: str,
        symbol: Optional[str],
        backlog: int,
        capacity: int,
        is_degraded: bool,
    ) -> None:
        if not self._enabled:
            return
        label_symbol = symbol or "unknown"
        ratio = backlog / capacity if capacity > 0 else 0.0
        payload = {
            "timestamp": self._utc_timestamp(),
            "metric": "queue_depth",
            "stream": stream,
            "symbol": label_symbol,
            "backlog": backlog,
            "capacity": capacity,
            "ratio": ratio,
            "degraded": is_degraded,
        }
        with self._lock:
            gauge = self._gauge(
                "stream_queue_backlog",
                "Current backlog of stream pipelines",
                ("stream", "symbol"),
            )
            ratio_gauge = self._gauge(
                "stream_queue_ratio",
                "Fill ratio of stream pipelines",
                ("stream", "symbol"),
            )
            if gauge is not None:
                gauge.labels(stream=stream, symbol=label_symbol).set(backlog)
            if ratio_gauge is not None:
                ratio_gauge.labels(stream=stream, symbol=label_symbol).set(ratio)
            self._daily_queue[(stream, label_symbol)].observe(backlog, capacity)
        self._emit_json(payload)
        self.record_flow(stream, label_symbol)
        if ratio >= self._thresholds.backpressure_ratio:
            self._emit_alert(
                "backpressure",
                {
                    "stream": stream,
                    "symbol": label_symbol,
                    "ratio": ratio,
                    "backlog": backlog,
                    "capacity": capacity,
                },
            )

    def increment_migration(self, stream: str, symbol: str) -> None:
        if not self._enabled:
            return
        payload = {
            "timestamp": self._utc_timestamp(),
            "metric": "stream_migration",
            "stream": stream,
            "symbol": symbol,
        }
        with self._lock:
            counter = self._counter(
                "stream_migrations_total",
                "Total number of stream migrations",
                ("stream",),
            )
            if counter is not None:
                counter.labels(stream=stream).inc()
            self._daily_migrations[stream] += 1
        self._emit_json(payload)

    def record_resync_trigger(
        self,
        stream: str,
        symbol: Optional[str],
        reason: str,
    ) -> None:
        if not self._enabled:
            return
        label_symbol = symbol or "unknown"
        payload = {
            "timestamp": self._utc_timestamp(),
            "metric": "resync_trigger",
            "stream": stream,
            "symbol": label_symbol,
            "reason": reason,
        }
        with self._lock:
            if reason.lower().startswith("таймаут") or "silence" in reason.lower():
                key = (stream, label_symbol)
                now = time.monotonic()
                previous = self._silence_state.get(key)
                self._silence_state[key] = now
                if (
                    previous is not None
                    and now - previous >= self._thresholds.silence_timeout_s
                ):
                    self._emit_alert(
                        "extended_silence",
                        {
                            "stream": stream,
                            "symbol": label_symbol,
                            "duration": now - previous,
                        },
                    )
        self._emit_json(payload)

    def observe_resync_duration(
        self,
        symbol: str,
        reason: str,
        duration: float,
        success: bool,
    ) -> None:
        if not self._enabled:
            return
        payload = {
            "timestamp": self._utc_timestamp(),
            "metric": "resync_duration",
            "symbol": symbol,
            "reason": reason,
            "duration": duration,
            "success": success,
        }
        with self._lock:
            histogram = self._histogram(
                "stream_resync_duration_seconds",
                "Duration of resync tasks",
                ("symbol", "reason", "success"),
            )
            if histogram is not None:
                histogram.labels(
                    symbol=symbol,
                    reason=reason,
                    success="1" if success else "0",
                ).observe(duration)
            self._daily_resync[(symbol, reason)].observe(duration)
        self._emit_json(payload)

    def record_flow(self, stream: str, symbol: str) -> None:
        if not self._enabled:
            return
        key = (stream, symbol)
        with self._lock:
            timestamp = self._silence_state.pop(key, None)
        if timestamp is not None:
            payload = {
                "timestamp": self._utc_timestamp(),
                "metric": "silence_recovered",
                "stream": stream,
                "symbol": symbol,
                "silence_duration": time.monotonic() - timestamp,
            }
            self._emit_json(payload)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _emit_json(self, payload: Mapping[str, object]) -> None:
        sink = self._json_sink
        if sink is None:
            return
        try:
            sink(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        except Exception:
            pass

    def _emit_alert(self, alert_type: str, details: Mapping[str, object]) -> None:
        alert_payload = {
            "timestamp": self._utc_timestamp(),
            "level": "ALERT",
            "alert": alert_type,
            **dict(details),
        }
        self._emit_json(alert_payload)

    def _ensure_prometheus(self, port: int) -> None:
        if CollectorRegistry is None or Gauge is None or Counter is None:
            return
        with self._lock:
            if self._registry is None:
                self._registry = CollectorRegistry()
            if not self._prometheus_started:
                try:
                    start_http_server(port, registry=self._registry)
                    self._prometheus_started = True
                except Exception:
                    self._prometheus_started = False

    def _ensure_daily_reporter(self) -> None:
        with self._lock:
            if self._daily_thread and self._daily_thread.is_alive():
                return
            thread = threading.Thread(
                target=self._daily_loop,
                name="metrics-daily-report",
                daemon=True,
            )
            self._daily_thread = thread
            thread.start()

    def _daily_loop(self) -> None:
        while not self._stop_event.is_set():
            wait_seconds = self._seconds_until_report()
            if wait_seconds <= 0:
                wait_seconds = 60.0
            interrupted = self._stop_event.wait(wait_seconds)
            if interrupted:
                break
            self._publish_daily_summary()

    def _seconds_until_report(self) -> float:
        now = datetime.now(timezone.utc)
        target = now.replace(
            hour=self._daily_report_hour,
            minute=0,
            second=15,
            microsecond=0,
        )
        if target <= now:
            target = target + timedelta(days=1)
        return (target - now).total_seconds()

    def _publish_daily_summary(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            commands = {
                f"{stream}:{command}": bucket.as_dict()
                for (stream, command), bucket in self._daily_commands.items()
            }
            resyncs = {
                f"{symbol}:{reason}": bucket.as_dict()
                for (symbol, reason), bucket in self._daily_resync.items()
            }
            queues = {
                f"{stream}:{symbol}": snapshot.as_dict()
                for (stream, symbol), snapshot in self._daily_queue.items()
            }
            migrations = dict(self._daily_migrations)
            self._daily_commands.clear()
            self._daily_resync.clear()
            self._daily_queue.clear()
            self._daily_migrations.clear()
        summary = {
            "timestamp": self._utc_timestamp(),
            "report": "daily_metrics",
            "commands": commands,
            "resyncs": resyncs,
            "queues": queues,
            "migrations": migrations,
        }
        self._emit_json(summary)

    def _prune_history(self, history: Deque[Tuple[float, str]], now: float) -> None:
        window = self._thresholds.resubscribe_window_s
        cutoff = now - window
        while history and history[0][0] < cutoff:
            history.popleft()

    def _check_resubscribe_ratio(
        self,
        stream: str,
        history: Deque[Tuple[float, str]],
    ) -> None:
        total = len(history)
        if total < 5:
            return
        resubscribe = sum(1 for _, command in history if command == "resubscribe")
        subscribe = sum(1 for _, command in history if command == "subscribe")
        if subscribe <= 0:
            return
        ratio = resubscribe / max(subscribe, 1)
        if ratio >= self._thresholds.resubscribe_ratio:
            self._emit_alert(
                "high_resubscribe_ratio",
                {
                    "stream": stream,
                    "ratio": ratio,
                    "window": self._thresholds.resubscribe_window_s,
                    "resubscribe": resubscribe,
                    "subscribe": subscribe,
                },
            )

    def _gauge(
        self,
        name: str,
        documentation: str,
        labels: Iterable[str],
    ) -> Optional[Gauge]:
        if self._registry is None or Gauge is None:
            return None
        gauge = self._gauges.get(name)
        if gauge is None:
            try:
                gauge = Gauge(name, documentation, labelnames=tuple(labels), registry=self._registry)
                self._gauges[name] = gauge
            except ValueError:
                gauge = self._gauges.get(name)
        return gauge

    def _counter(
        self,
        name: str,
        documentation: str,
        labels: Iterable[str],
    ) -> Optional[Counter]:
        if self._registry is None or Counter is None:
            return None
        counter = self._counters.get(name)
        if counter is None:
            try:
                counter = Counter(name, documentation, labelnames=tuple(labels), registry=self._registry)
                self._counters[name] = counter
            except ValueError:
                counter = self._counters.get(name)
        return counter

    def _histogram(
        self,
        name: str,
        documentation: str,
        labels: Iterable[str],
    ) -> Optional[Histogram]:
        if self._registry is None or Histogram is None:
            return None
        histogram = self._histograms.get(name)
        if histogram is None:
            try:
                histogram = Histogram(
                    name,
                    documentation,
                    labelnames=tuple(labels),
                    registry=self._registry,
                )
                self._histograms[name] = histogram
            except ValueError:
                histogram = self._histograms.get(name)
        return histogram

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()


METRICS = MetricsService()


__all__ = ["METRICS", "MetricsThresholds"]
