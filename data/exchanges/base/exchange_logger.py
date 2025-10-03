from typing import Callable, Optional

from .resync_reason import ResyncReason
from utils.timez import get_current_time


class ExchangeLogger:
    def __init__(
        self,
        name: str,
        writer: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._name = name
        self._writer = writer

    def log(self, message: str) -> None:
        timestamp = get_current_time().astimezone()
        formatted = f"{timestamp:%H:%M:%S} {message}"
        if self._writer:
            self._writer(formatted)
        else:
            print(formatted)

    def log_resync(self, reason: ResyncReason, details: str | None = None) -> None:
        suffix = f" {details}" if details else ""
        self.log(f"Ресинк книги. Причина: {reason.value}.{suffix}")


__all__ = ["ExchangeLogger"]
