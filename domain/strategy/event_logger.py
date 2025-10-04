from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.models import LogLine
from .types import LogWriter


@dataclass
class EventLogger:
    write: LogWriter

    def log(self, message: str, timestamp: datetime) -> None:
        entry = LogLine(timestamp=timestamp, message=message, level="INFO")
        self.write(entry)


__all__ = ["EventLogger"]
