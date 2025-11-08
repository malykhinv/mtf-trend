from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Deque, Dict

from .limits import StreamLimit


@dataclass
class RateLimiter:
    """Simple window-based rate limiter used by the stream manager."""

    limit: int
    window: timedelta
    _events: Deque[datetime] = field(default_factory=deque, init=False, repr=False)

    def __post_init__(self) -> None:  # pragma: no cover - simple initializer
        if self.window <= timedelta(0):
            object.__setattr__(self, "window", timedelta(seconds=1))

    def _trim(self, timestamp: datetime) -> None:
        while self._events and timestamp - self._events[0] >= self.window:
            self._events.popleft()

    def has_capacity(self, timestamp: datetime) -> bool:
        if self.limit <= 0:
            return True
        self._trim(timestamp)
        return len(self._events) < self.limit

    def commit(self, timestamp: datetime) -> None:
        if self.limit <= 0:
            return
        self._trim(timestamp)
        self._events.append(timestamp)

    def remaining(self, timestamp: datetime) -> float:
        if self.limit <= 0:
            return float("inf")
        self._trim(timestamp)
        return max(self.limit - len(self._events), 0)


class CommandBudget:
    """Enforces Binance command rate limits across shared sessions."""

    __slots__ = (
        "_steady",
        "_burst",
        "_reserve_limit",
        "_reserve_used",
        "_lock",
        "_failure_delay",
    )

    def __init__(self, limit: StreamLimit) -> None:
        self._steady = RateLimiter(limit.steady_per_min, timedelta(minutes=1))
        self._burst = RateLimiter(limit.burst_per_5s, timedelta(seconds=5))
        self._reserve_limit = max(0, limit.resubscribe_buffer)
        self._reserve_used = 0
        self._lock = Lock()
        self._failure_delay = 0.0

    def consume(self, *, priority: str = "normal") -> bool:
        """Consume from the configured budget."""

        use_reserve = priority != "normal"
        now = datetime.now(tz=timezone.utc)
        with self._lock:
            steady_ok = self._steady.has_capacity(now)
            burst_ok = self._burst.has_capacity(now)
            if not steady_ok or not burst_ok:
                wait = 0.0
                if not steady_ok and self._steady.limit > 0:
                    wait = max(wait, self._steady.window.total_seconds())
                if not burst_ok and self._burst.limit > 0:
                    wait = max(wait, self._burst.window.total_seconds())
                self._failure_delay = wait
                return False
            self._steady.commit(now)
            self._burst.commit(now)
            self._failure_delay = 0.0
            if use_reserve and self._reserve_used < self._reserve_limit:
                self._reserve_used += 1
            elif not use_reserve and self._reserve_used > 0:
                self._reserve_used -= 1
            return True

    def failure_delay(self) -> float:
        with self._lock:
            if self._failure_delay > 0.0:
                return self._failure_delay
            waits: list[float] = []
            if self._steady.limit > 0:
                waits.append(self._steady.window.total_seconds())
            if self._burst.limit > 0:
                waits.append(self._burst.window.total_seconds())
            return max(waits) if waits else 0.0

    def reserve_remaining(self) -> int:
        with self._lock:
            return max(self._reserve_limit - self._reserve_used, 0)

    def debug_state(self) -> Dict[str, float]:
        now = datetime.now(tz=timezone.utc)
        with self._lock:
            steady_remaining = self._steady.remaining(now)
            burst_remaining = self._burst.remaining(now)
            reserve_remaining = max(self._reserve_limit - self._reserve_used, 0)
            failure_delay = self._failure_delay if self._failure_delay > 0.0 else 0.0
        return {
            "steady_remaining": float(steady_remaining),
            "steady_limit": float(self._steady.limit),
            "burst_remaining": float(burst_remaining),
            "burst_limit": float(self._burst.limit),
            "reserve_remaining": float(reserve_remaining),
            "reserve_limit": float(self._reserve_limit),
            "failure_delay": float(failure_delay),
        }


__all__ = ["RateLimiter", "CommandBudget"]
