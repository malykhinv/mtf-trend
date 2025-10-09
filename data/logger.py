from __future__ import annotations

import os
import sys
from typing import Callable, NamedTuple, TextIO, TYPE_CHECKING

if TYPE_CHECKING:
    from domain.models import LogLine

LogSink = Callable[[str], None]
LogLineWriter = Callable[["LogLine"], None]


class _LevelStyle(NamedTuple):
    color: str
    emoji: str


_RESET_COLOR = "\033[0m"
_LEVEL_STYLES: dict[str, _LevelStyle] = {
    "INFO": _LevelStyle(color="", emoji=""),
    "ERROR": _LevelStyle(color="\033[31m", emoji="❗"),
    "TRADING": _LevelStyle(color="\033[32m", emoji="💹"),
}


def _supports_color_output(stream: TextIO | None) -> bool:
    if stream is None:
        return False
    try:
        if stream.isatty():
            return True
    except Exception:
        pass
    if os.environ.get("PYCHARM_HOSTED") == "1" and stream in {sys.stdout, sys.stderr}:
        return True
    return False


def _print_sink(message: str) -> None:
    print(message)

setattr(_print_sink, "_supports_color", _supports_color_output(getattr(sys, "stdout", None)))


def create_text_log_sink(stream: TextIO | None = None) -> LogSink:
    if stream is None:
        return _print_sink

    def _write(message: str) -> None:
        stream.write(f"{message}\n")
        stream.flush()

    setattr(_write, "_supports_color", _supports_color_output(stream))

    return _write


def create_log_writer(sink: LogSink | None = None) -> LogLineWriter:
    text_sink = sink or _print_sink
    use_color_attr = getattr(text_sink, "_supports_color", None)
    if use_color_attr is None:
        use_color = os.environ.get("PYCHARM_HOSTED") == "1"
    else:
        use_color = bool(use_color_attr)

    def _decorate(message: str, level: str) -> str:
        style = _LEVEL_STYLES.get(level, _LEVEL_STYLES["INFO"])
        if use_color and style.color:
            return f"{style.color}{message}{_RESET_COLOR}"
        if not use_color and style.emoji:
            return f"{style.emoji} {message}"
        return message

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
        formatted = f"{timestamp:%H:%M:%S} {message}"
        text_sink(_decorate(formatted, level))

    return _write


__all__ = ["LogSink", "LogLineWriter", "create_text_log_sink", "create_log_writer"]
