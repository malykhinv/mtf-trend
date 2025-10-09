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

    def _strip_prefix(message: str) -> str:
        stripped = message.strip()
        if len(stripped) >= 9 and stripped[2] == ":" and stripped[5] == ":":
            if stripped[:2].isdigit() and stripped[3:5].isdigit() and stripped[6:8].isdigit():
                if stripped[8] == " ":
                    return stripped[9:]
        return stripped

    def _write(entry: LogLine) -> None:
        timestamp = entry.timestamp.astimezone()
        message = _strip_prefix(entry.message)
        text_sink(f"{timestamp:%H:%M:%S} {message}")

    return _write


__all__ = ["LogSink", "LogLineWriter", "create_text_log_sink", "create_log_writer"]
