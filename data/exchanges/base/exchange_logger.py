from typing import Callable, Optional

from domain.models import LogLine
from utils.timez import get_current_time
from .resync_reason import ResyncReason


class ExchangeLogger:
    def __init__(
        self,
        name: str,
        writer: Optional[Callable[[LogLine], None]] = None,
    ) -> None:
        self._name = name
        self._writer = writer

    def log(self, message: str, level: str = "INFO") -> None:
        timestamp = get_current_time().astimezone()
        if self._writer:
            self._writer(LogLine(timestamp=timestamp, message=message, level=level))
        else:
            level_tag = level.upper() if level else "INFO"
            print(f"{timestamp:%H:%M:%S} [{level_tag}] {message}")

    def log_info(self, message: str) -> None:
        self.log(message, level="INFO")

    def log_error(self, message: str) -> None:
        self.log(message, level="ERROR")

    def log_trade(self, message: str) -> None:
        self.log(message, level="TRADE")

    def log_resync(self, reason: ResyncReason, details: str | None = None) -> None:
        suffix = f" {details}" if details else ""
        self.log_error(f"Ресинк книги. Причина: {reason.value}.{suffix}")


__all__ = ["ExchangeLogger"]
