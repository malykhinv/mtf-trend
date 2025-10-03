from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ._timezone import ensure_current_timezone


@dataclass(frozen=True, slots=True)
class LogLine:
    timestamp: datetime
    message: str
    level: str

    def __post_init__(self) -> None:
        ensure_current_timezone(self.timestamp)


__all__ = ["LogLine"]
