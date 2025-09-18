from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Deque, Tuple

from ..models.entities import Signal


@dataclass(slots=True)
class DedupRecord:
    key: str
    created_at: datetime


class DeduplicationPolicy:
    def __init__(self, ttl_seconds: int = 600, max_records: int = 1000) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._records: Deque[DedupRecord] = deque(maxlen=max_records)
        self._lock = Lock()

    def _make_key(self, signal: Signal) -> str:
        return f"{signal.candle.symbol}:{signal.side}:{signal.candle.timeframe}:{signal.direction}"

    def should_accept(self, signal: Signal) -> Tuple[bool, str | None]:
        with self._lock:
            now = signal.triggered_at
            key = self._make_key(signal)
            self._records = deque(
                [record for record in self._records if now - record.created_at <= self._ttl],
                maxlen=self._records.maxlen,
            )
            for record in self._records:
                if record.key == key:
                    return False, key
            self._records.append(DedupRecord(key=key, created_at=now))
            return True, key
