from typing import Callable, Mapping, Optional

from utils.timez import get_current_time
from .resync_reason import ResyncReason


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

    def metric(
        self,
        name: str,
        value: float,
        *,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Emit a monitoring datapoint using the configured writer."""

        timestamp = get_current_time().astimezone()
        tag_section = ""
        if tags:
            tag_section = " " + ",".join(
                f"{key}={val}" for key, val in sorted(tags.items())
            )
        message = f"{timestamp:%H:%M:%S} METRIC {name} value={value:.6f}{tag_section}"
        if self._writer:
            self._writer(message)
        else:
            print(message)


__all__ = ["ExchangeLogger"]
