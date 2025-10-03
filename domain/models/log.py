"""Models representing structured log entries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._timezone import ensure_belgrade_timezone


@dataclass(frozen=True, slots=True)
class LogLine:
    """Structured log line produced by the application."""

    timestamp: datetime
    message: str
    level: str

    def __post_init__(self) -> None:
        ensure_belgrade_timezone(self.timestamp)


__all__ = ["LogLine"]
