"""Console status rendering for anomaly live2.

Keeps a single operator status block pinned at the bottom of the terminal,
matching the legacy live runtime behavior: normal log lines clear/repaint the
status block, while status updates overwrite the previous block instead of
spamming the console.
"""

from __future__ import annotations

import re
import shutil
import sys
import threading
from collections.abc import Callable


class Live2StatusLogger:
    """Small v1-style console logger with one repaintable status block."""

    _ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

    def __init__(self, logger: Callable[[str], None]) -> None:
        self._logger = logger
        self._inline_status_enabled = logger is print and sys.stdout.isatty()
        self._lock = threading.RLock()
        self._status_line_open = False
        self._status_line_rows = 0
        self._last_status_message: str | None = None
        self._last_status_highlight = False

    @property
    def inline_status_enabled(self) -> bool:
        return self._inline_status_enabled

    def __call__(self, message: str) -> None:
        with self._lock:
            repaint_status = self._inline_status_enabled and self._last_status_message is not None
            if self._inline_status_enabled:
                self._clear_status_line_if_needed()
            else:
                self._finish_status_line_if_needed()
            self._logger(message)
            if repaint_status and self._last_status_message is not None:
                self._paint_status(self._last_status_message, highlight=self._last_status_highlight)

    def alert(self, message: str) -> None:
        with self._lock:
            repaint_status = self._inline_status_enabled and self._last_status_message is not None
            if self._inline_status_enabled:
                self._clear_status_line_if_needed()
            rendered = f"\033[1;31m{message}\033[0m" if self._inline_status_enabled else message
            self._logger(rendered)
            if repaint_status and self._last_status_message is not None:
                self._paint_status(self._last_status_message, highlight=self._last_status_highlight)

    def status(self, message: str, *, highlight: bool = False) -> None:
        with self._lock:
            self._last_status_message = str(message)
            self._last_status_highlight = bool(highlight)
            if not self._inline_status_enabled:
                self._logger(message)
                return
            self._paint_status(self._last_status_message, highlight=self._last_status_highlight)

    def finish_status(self) -> None:
        with self._lock:
            self._last_status_message = None
            self._last_status_highlight = False
            if self._inline_status_enabled:
                self._clear_status_line_if_needed()
            else:
                self._finish_status_line_if_needed()

    def _paint_status(self, message: str, *, highlight: bool) -> None:
        rendered = f"\033[1;33m{message}\033[0m" if highlight else message
        terminal_columns = self._terminal_columns()
        self._clear_status_line_if_needed()
        sys.stdout.write(rendered)
        sys.stdout.flush()
        self._status_line_rows = self._rendered_rows(rendered, terminal_columns)
        self._status_line_open = True

    def _finish_status_line_if_needed(self) -> None:
        if not self._inline_status_enabled or not self._status_line_open:
            return
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._status_line_open = False
        self._status_line_rows = 0

    def _clear_status_line_if_needed(self) -> None:
        if not self._status_line_open:
            return
        rows = max(1, self._status_line_rows)
        sys.stdout.write("\r")
        for _ in range(rows - 1):
            sys.stdout.write("\033[1A")
        sys.stdout.write("\r\033[J")
        self._status_line_open = False
        self._status_line_rows = 0

    @staticmethod
    def _terminal_columns() -> int:
        return max(20, shutil.get_terminal_size(fallback=(120, 20)).columns)

    @classmethod
    def _rendered_rows(cls, rendered: str, terminal_columns: int) -> int:
        visible = cls._ANSI_RE.sub("", rendered)
        if not visible:
            return 1
        columns = max(1, terminal_columns)
        rows = 0
        for line in visible.split("\n"):
            rows += max(1, (len(line) - 1) // columns + 1) if line else 1
        return max(1, rows)
