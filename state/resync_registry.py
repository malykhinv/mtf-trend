from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from threading import Lock
from typing import Dict, Iterable, Optional


@dataclass(frozen=True)
class ResyncStatus:
    """Represents the recovery status of a trading symbol."""

    symbol: str
    attempts: int
    last_success_at: Optional[datetime]
    last_failure_at: Optional[datetime]
    last_failure_reason: Optional[str]
    worker_id: Optional[str]
    started_at: Optional[datetime]


class ResyncRegistry:
    """Thread-safe registry that keeps track of resync attempts."""

    def __init__(self) -> None:
        self._records: Dict[str, ResyncStatus] = {}
        self._lock = Lock()

    def record_start(self, symbol: str, worker_id: str, started_at: datetime) -> ResyncStatus:
        with self._lock:
            status = self._records.get(symbol)
            if status is None:
                status = ResyncStatus(
                    symbol=symbol,
                    attempts=0,
                    last_success_at=None,
                    last_failure_at=None,
                    last_failure_reason=None,
                    worker_id=None,
                    started_at=None,
                )
            status = replace(
                status,
                attempts=status.attempts + 1,
                worker_id=worker_id,
                started_at=started_at,
            )
            self._records[symbol] = status
            return status

    def record_success(self, symbol: str, completed_at: datetime) -> ResyncStatus:
        with self._lock:
            status = self._records.get(symbol)
            if status is None:
                status = ResyncStatus(
                    symbol=symbol,
                    attempts=0,
                    last_success_at=completed_at,
                    last_failure_at=None,
                    last_failure_reason=None,
                    worker_id=None,
                    started_at=None,
                )
            else:
                status = replace(
                    status,
                    last_success_at=completed_at,
                    last_failure_at=None,
                    last_failure_reason=None,
                    worker_id=None,
                    started_at=None,
                )
            self._records[symbol] = status
            return status

    def record_failure(
        self, symbol: str, failure_at: datetime, reason: str, *, worker_id: Optional[str] = None
    ) -> ResyncStatus:
        with self._lock:
            status = self._records.get(symbol)
            if status is None:
                status = ResyncStatus(
                    symbol=symbol,
                    attempts=1,
                    last_success_at=None,
                    last_failure_at=failure_at,
                    last_failure_reason=reason,
                    worker_id=worker_id,
                    started_at=None,
                )
            else:
                status = replace(
                    status,
                    last_failure_at=failure_at,
                    last_failure_reason=reason,
                    worker_id=worker_id if worker_id is not None else status.worker_id,
                    started_at=None,
                )
            self._records[symbol] = status
            return status

    def status(self, symbol: str) -> Optional[ResyncStatus]:
        with self._lock:
            status = self._records.get(symbol)
            return None if status is None else replace(status)

    def all_statuses(self) -> Iterable[ResyncStatus]:
        with self._lock:
            return [replace(status) for status in self._records.values()]

    def stalled(self, timeout: timedelta, now: datetime) -> Iterable[ResyncStatus]:
        with self._lock:
            result = []
            for status in self._records.values():
                started_at = status.started_at
                if started_at is None:
                    continue
                if now - started_at >= timeout:
                    result.append(replace(status))
            return result


__all__ = ["ResyncRegistry", "ResyncStatus"]
