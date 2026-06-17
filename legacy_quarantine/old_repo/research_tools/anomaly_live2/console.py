"""Console status rendering for anomaly live2.

Only two operator UI surfaces are allowed for live2 stdout: startup warmup
progress and the live status grid. All ordinary logs, retry diagnostics, and
tracebacks are routed to run artifacts so they cannot split or corrupt the
repainted operator block.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import TextIO


class Live2ConsoleIsolation:
    """Route non-operator stdout/stderr/logging output into artifact files.

    The status logger keeps its original terminal stream and remains the only
    writer allowed to touch the operator console. This is intentionally scoped
    to live2 runtime only; artifacts remain the source of detailed diagnostics.
    """

    def __init__(self, *, output_dir: Path, status_logger: Live2StatusLogger) -> None:
        self.output_dir = Path(output_dir)
        self.status_logger = status_logger
        self._old_stdout: TextIO | None = None
        self._old_stderr: TextIO | None = None
        self._stdout_file: TextIO | None = None
        self._stderr_file: TextIO | None = None
        self._handler_streams: list[tuple[logging.StreamHandler, TextIO | object]] = []

    def __enter__(self) -> Live2ConsoleIsolation:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._old_stdout = sys.stdout
        self._old_stderr = sys.stderr
        self._stdout_file = (self.output_dir / "live2_suppressed_stdout.log").open(
            "a",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )
        self._stderr_file = (self.output_dir / "live2_suppressed_stderr.log").open(
            "a",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )
        sys.stdout = self._stdout_file  # type: ignore[assignment]
        sys.stderr = self._stderr_file  # type: ignore[assignment]
        self._redirect_existing_console_log_handlers()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        for handler, stream in reversed(self._handler_streams):
            try:
                handler.setStream(stream)  # type: ignore[arg-type]
            except Exception:
                pass
        self._handler_streams.clear()
        if self._old_stdout is not None:
            sys.stdout = self._old_stdout  # type: ignore[assignment]
        if self._old_stderr is not None:
            sys.stderr = self._old_stderr  # type: ignore[assignment]
        for stream in (self._stdout_file, self._stderr_file):
            if stream is None:
                continue
            try:
                stream.flush()
                stream.close()
            except Exception:
                pass
        self._stdout_file = None
        self._stderr_file = None

    def _redirect_existing_console_log_handlers(self) -> None:
        if self._stderr_file is None:
            return
        for logger in self._iter_loggers():
            for handler in logger.handlers:
                if not isinstance(handler, logging.StreamHandler):
                    continue
                if isinstance(handler, logging.FileHandler):
                    continue
                current_stream = getattr(handler, "stream", None)
                if current_stream is self._stderr_file:
                    continue
                self._handler_streams.append((handler, current_stream))
                try:
                    handler.setStream(self._stderr_file)
                except Exception:
                    pass

    @staticmethod
    def _iter_loggers() -> list[logging.Logger]:
        loggers: list[logging.Logger] = [logging.getLogger()]
        manager = logging.Logger.manager
        for value in manager.loggerDict.values():
            if isinstance(value, logging.Logger):
                loggers.append(value)
        return loggers


class Live2StatusLogger:
    """Operator-only repainting console surface for live2."""

    _ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

    def __init__(self, logger: Callable[[str], None]) -> None:
        self._logger = logger
        self._output_stream: TextIO = sys.stdout
        self._status_output_enabled = True
        self._log_output_enabled = True
        self._enable_windows_virtual_terminal()
        self._inline_status_enabled = self._resolve_inline_status_enabled(logger)
        self._lock = threading.RLock()
        self._screen_owned = False
        self._status_line_open = False
        self._status_line_rows = 0
        self._last_status_message: str | None = None
        self._last_status_highlight = False

    @property
    def inline_status_enabled(self) -> bool:
        return self._inline_status_enabled

    def isolate_console(self, output_dir: Path) -> Live2ConsoleIsolation:
        return Live2ConsoleIsolation(output_dir=output_dir, status_logger=self)

    def _resolve_inline_status_enabled(self, logger: Callable[[str], None]) -> bool:
        mode = os.environ.get("LIVE2_STATUS_MODE", "").strip().lower()
        if mode in {"silent", "off", "none"}:
            self._status_output_enabled = False
            return False
        if mode in {"inline", "ansi", "repaint"}:
            return True
        if mode in {"snapshot", "plain", "line", "non_inline", "non-inline"}:
            return False
        # Live2 operator UI must repaint instead of printing snapshots. Prefer
        # ANSI repaint by default; unsupported terminals can opt out explicitly.
        return logger is print

    @staticmethod
    def _enable_windows_virtual_terminal() -> None:
        if os.name != "nt":
            return
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if handle == 0 or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            return

    def __call__(self, message: str) -> None:
        # Non-UI logs are forbidden on the operator console after P464. Keep the
        # callable contract for Telegram/etc., but route nothing to stdout here.
        with self._lock:
            if not self._log_output_enabled:
                return
            return

    def alert(self, message: str) -> None:
        # Safety/integrity alerts belong in artifacts/Telegram. Printing them to
        # stdout corrupts the only allowed live grid surface.
        with self._lock:
            if not self._log_output_enabled:
                return
            return

    def set_log_output_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._log_output_enabled = bool(enabled)

    def status(self, message: str, *, highlight: bool = False) -> None:
        with self._lock:
            self._last_status_message = str(message)
            self._last_status_highlight = bool(highlight)
            if not self._status_output_enabled or not self._inline_status_enabled:
                return
            self._paint_status(self._last_status_message, highlight=self._last_status_highlight)

    def finish_status(self) -> None:
        with self._lock:
            self._last_status_message = None
            self._last_status_highlight = False
            if self._inline_status_enabled and self._screen_owned:
                self._write("\033[?25h\n")
                self._flush()
            self._screen_owned = False
            self._status_line_open = False
            self._status_line_rows = 0

    def _paint_status(self, message: str, *, highlight: bool) -> None:
        rendered = f"\033[1;33m{message}\033[0m" if highlight else message
        # Full-screen repaint is more robust than trying to clear only the
        # previous block: unrelated console output cannot leave fragments behind,
        # and variable-height grids do not duplicate in PowerShell.
        self._write("\033[?25l\033[H\033[J")
        self._write(rendered)
        self._flush()
        self._screen_owned = True
        self._status_line_open = True
        self._status_line_rows = self._rendered_rows(rendered, self._terminal_columns())

    def _finish_status_line_if_needed(self) -> None:
        if not self._inline_status_enabled or not self._status_line_open:
            return
        self._write("\n")
        self._flush()
        self._status_line_open = False
        self._status_line_rows = 0

    def _clear_status_line_if_needed(self) -> None:
        if not self._status_line_open:
            return
        self._write("\033[H\033[J")
        self._flush()
        self._status_line_open = False
        self._status_line_rows = 0

    def _write(self, text: str) -> None:
        self._output_stream.write(text)

    def _flush(self) -> None:
        self._output_stream.flush()

    @staticmethod
    def _terminal_columns() -> int:
        return max(20, shutil.get_terminal_size(fallback=(120, 20)).columns)

    @classmethod
    def _rendered_rows(cls, rendered: str, terminal_columns: int) -> int:
        visible = cls._ANSI_RE.sub("", rendered).expandtabs(4)
        if not visible:
            return 1
        columns = max(1, terminal_columns)
        rows = 0
        for line in visible.split("\n"):
            rows += max(1, (len(line) - 1) // columns + 1) if line else 1
        return max(1, rows)
