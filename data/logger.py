from __future__ import annotations

from typing import Callable, TextIO, TYPE_CHECKING

if TYPE_CHECKING:
    from domain.models import LogLine

LogSink = Callable[[str], None]
LogLineWriter = Callable[["LogLine"], None]


def _print_sink(message: str) -> None:
    print(message)


def create_text_log_sink(stream: TextIO | None = None) -> LogSink:
    if stream is None:
        return _print_sink

    def _write(message: str) -> None:
        stream.write(f"{message}\n")
        stream.flush()

    return _write


def create_log_writer(sink: LogSink | None = None) -> LogLineWriter:
    text_sink = sink or _print_sink

    def _write(entry: LogLine) -> None:
        timestamp = entry.timestamp.astimezone()
        text_sink(f"{timestamp:%H:%M:%S} {entry.message}")

    return _write


__all__ = ["LogSink", "LogLineWriter", "create_text_log_sink", "create_log_writer"]
