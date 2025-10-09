from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TextIO, TYPE_CHECKING

if TYPE_CHECKING:
    from domain.models import LogLine

LogSink = Callable[[str], None]
LogLineWriter = Callable[["LogLine"], None]


RESET_COLOR = "\x1b[0m"


@dataclass(frozen=True)
class _LogStyle:
    color: str = ""
    emoji: str = ""


_LEVEL_STYLES: dict[str, _LogStyle] = {
    "ERROR": _LogStyle(color="\x1b[31m", emoji="❗"),
    "TRADE": _LogStyle(color="\x1b[36m", emoji="💱"),
    "WARNING": _LogStyle(color="\x1b[33m", emoji="⚠"),
}
_DEFAULT_STYLE = _LogStyle()


def _print_sink(message: str) -> None:
    print(message)


_print_sink._supports_color = True  # type: ignore[attr-defined]


def _get_log_style(level: str) -> _LogStyle:
    return _LEVEL_STYLES.get(level, _DEFAULT_STYLE)


def format_log_message(timestamp: datetime, level: str, message: str, *, use_color: bool) -> str:
    level_tag = level.upper() if level else "INFO"
    formatted = f"{timestamp:%H:%M:%S} [{level_tag}] {message}"
    style = _get_log_style(level_tag)
    if use_color and style.color:
        return f"{style.color}{formatted}{RESET_COLOR}"
    if not use_color and style.emoji:
        return f"{formatted} {style.emoji}"
    return formatted


def create_text_log_sink(stream: TextIO | None = None) -> LogSink:
    if stream is None:
        return _print_sink

    def _write(message: str) -> None:
        stream.write(f"{message}\n")
        stream.flush()

    supports_color = bool(getattr(stream, "isatty", lambda: False)())
    _write._supports_color = supports_color  # type: ignore[attr-defined]
    return _write


def create_log_writer(sink: LogSink | None = None) -> LogLineWriter:
    text_sink = sink or _print_sink
    supports_color = bool(getattr(text_sink, "_supports_color", sink is None))

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
        level = entry.level.upper() if entry.level else "INFO"
        text_sink(format_log_message(timestamp, level, message, use_color=supports_color))

    return _write


__all__ = [
    "LogSink",
    "LogLineWriter",
    "create_text_log_sink",
    "create_log_writer",
    "format_log_message",
]
