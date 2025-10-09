from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.models import LogLine
from .types import LogWriter


@dataclass
class EventLogger:
    write: LogWriter

    def log(self, message: str, timestamp: datetime, level: str = "INFO") -> None:
        entry = LogLine(timestamp=timestamp, message=message, level=level)
        self.write(entry)

    def log_info(self, message: str, timestamp: datetime) -> None:
        self.log(message, timestamp, level="INFO")

    def log_error(self, message: str, timestamp: datetime) -> None:
        self.log(message, timestamp, level="ERROR")

    def log_trade(self, message: str, timestamp: datetime) -> None:
        self.log(message, timestamp, level="TRADE")


__all__ = ["EventLogger"]
