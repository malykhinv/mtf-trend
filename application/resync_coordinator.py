from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional, Sequence

from data.exchanges import ResyncReason
from domain.models import OrderBookSnapshot
from state.resync_registry import ResyncRegistry, ResyncStatus
from utils.metrics import METRICS
from utils.timez import get_current_time


DepthMessage = dict[str, object]
SnapshotFetcher = Callable[[], OrderBookSnapshot]
SnapshotApplier = Callable[[OrderBookSnapshot], Sequence[DepthMessage]]
DeltaReplayer = Callable[[Sequence[DepthMessage]], None]
Validator = Callable[[], bool]
FailureCallback = Callable[[Exception], None]
SuccessCallback = Callable[[], None]
MonitorCallback = Callable[[ResyncStatus, str], None]
QuarantineCallback = Callable[[str], None]


@dataclass(frozen=True)
class ResyncTask:
    symbol: str
    reason: ResyncReason
    fetch_snapshot: SnapshotFetcher
    apply_snapshot: SnapshotApplier
    replay_delta: DeltaReplayer
    validate: Validator
    on_failure: Optional[FailureCallback] = None
    on_success: Optional[SuccessCallback] = None


class ResyncCoordinator:
    """Coordinates snapshot → replay → validate phases with bounded concurrency."""

    def __init__(
        self,
        *,
        max_parallel_rest: int = 2,
        registry: Optional[ResyncRegistry] = None,
        health_timeout: timedelta = timedelta(minutes=5),
        health_check_interval: timedelta = timedelta(seconds=30),
        monitor_callback: Optional[MonitorCallback] = None,
        quarantine_callback: Optional[QuarantineCallback] = None,
    ) -> None:
        self._max_parallel_rest = max(1, int(max_parallel_rest))
        self._registry = registry or ResyncRegistry()
        self._monitor_callback = monitor_callback
        self._quarantine_callback = quarantine_callback
        self._health_timeout = health_timeout
        self._health_interval = max(float(health_check_interval.total_seconds()), 1.0)

        self._queue: "queue.Queue[ResyncTask | None]" = queue.Queue()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._workers = [
            threading.Thread(target=self._worker_loop, name=f"resync-worker-{i}", daemon=True)
            for i in range(self._max_parallel_rest)
        ]
        self._health_thread = threading.Thread(
            target=self._health_loop,
            name="resync-health",
            daemon=True,
        )

        for worker in self._workers:
            worker.start()
        self._health_thread.start()

    @property
    def registry(self) -> ResyncRegistry:
        return self._registry

    def submit(self, task: ResyncTask) -> bool:
        """Submits a new resync task unless it is already pending."""

        with self._lock:
            if task.symbol in self._pending:
                return False
            self._pending.add(task.symbol)
            self._queue.put(task)
            return True

    def stop(self) -> None:
        """Stops all worker threads."""

        self._stop_event.set()
        for _ in self._workers:
            self._queue.put(None)
        for worker in self._workers:
            worker.join(timeout=1.0)
        self._health_thread.join(timeout=1.0)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if task is None:
                break
            self._execute_task(task)

    def _execute_task(self, task: ResyncTask) -> None:
        symbol = task.symbol
        worker_id = threading.current_thread().name
        started_at = get_current_time()
        self._registry.record_start(symbol, worker_id, started_at)
        end_time: datetime | None = None
        success = False
        try:
            snapshot = task.fetch_snapshot()
            pending = task.apply_snapshot(snapshot)
            task.replay_delta(pending)
            if not task.validate():
                raise RuntimeError(f"validation failed for {symbol}")
        except Exception as exc:  # noqa: BLE001
            end_time = get_current_time()
            self._registry.record_failure(symbol, end_time, str(exc), worker_id=worker_id)
            if task.on_failure is not None:
                try:
                    task.on_failure(exc)
                except Exception:
                    pass
        else:
            end_time = get_current_time()
            self._registry.record_success(symbol, end_time)
            if task.on_success is not None:
                try:
                    task.on_success()
                except Exception:
                    pass
            success = True
        finally:
            with self._lock:
                self._pending.discard(symbol)
        if end_time is None:
            end_time = get_current_time()
        duration = max((end_time - started_at).total_seconds(), 0.0)
        METRICS.observe_resync_duration(symbol, task.reason.value, duration, success)

    def _health_loop(self) -> None:
        while not self._stop_event.wait(self._health_interval):
            self.perform_health_check()

    def perform_health_check(self) -> Sequence[ResyncStatus]:
        now = get_current_time()
        stalled = tuple(self._registry.stalled(self._health_timeout, now))
        if not stalled:
            return stalled
        for status in stalled:
            message = (
                f"Resync stalled for {status.symbol}: attempts={status.attempts}, "
                f"worker={status.worker_id}, started_at={status.started_at}"
            )
            if self._monitor_callback is not None:
                try:
                    self._monitor_callback(status, message)
                except Exception:
                    pass
            if self._quarantine_callback is not None:
                try:
                    self._quarantine_callback(status.symbol)
                except Exception:
                    pass
        return stalled


__all__ = ["ResyncCoordinator", "ResyncTask"]
