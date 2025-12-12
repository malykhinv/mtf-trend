from __future__ import annotations

from heapq import heapify, heappop, heappush
from typing import Optional

from crypto_screener.config.config import AppConfig as cfg
from crypto_screener.domain.models.scheduled_task import ScheduledTask
from crypto_screener.utils.time import utc_now


class Scheduler:
    def __init__(self, tasks: Optional[list[ScheduledTask]] = None):
        self._tasks: list[ScheduledTask] = tasks or []
        heapify(self._tasks)

    def push(self, task: ScheduledTask) -> None:
        heappush(self._tasks, task)

    def pop_ready(self, now, capacity: Optional[int] = None) -> list[ScheduledTask]:
        ready: list[ScheduledTask] = []
        limit = capacity if capacity is not None else float("inf")
        while (
                self._tasks
                and self._tasks[0].next_run_at <= now
                and len(ready) < limit
        ):
            task = heappop(self._tasks)
            task.due_at = task.next_run_at
            ready.append(task)
        return ready

    def reschedule(self, task: ScheduledTask, has_active_capture: bool) -> None:
        interval = cfg.POLL_INTERVALS[task.timeframe]
        if has_active_capture:
            interval = cfg.CAPTURE_POLL_INTERVALS.get(task.timeframe, interval / 2)
        task.priority = -int(interval.total_seconds()) if has_active_capture else 0
        base_time = task.due_at or task.next_run_at
        task.next_run_at = base_time + interval
        task.due_at = None
        self.push(task)

    def get_next_sleep_timeout(self) -> float:
        if not self._tasks:
            return 0.5
        return min(max(0.0, (self._tasks[0].next_run_at - utc_now()).total_seconds()), 1.0)
