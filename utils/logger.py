"""Logging utilities with concise timestamped output."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class _ColorFormatter(logging.Formatter):
    """Adds ANSI colors for INFO/WARNING/ERROR levels in console output."""

    RESET = "\033[0m"
    COLORS = {
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[31m",
    }

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        color = self.COLORS.get(record.levelno)
        if color is None:
            return message
        return f"{color}{message}{self.RESET}"


def get_logger(
    name: str,
    level: int | str = logging.INFO,
    logs_dir: str | Path = "./logs",
) -> logging.Logger:
    """Create or return configured logger in `ЧЧ:ММ:СС Сообщение` format."""
    if isinstance(level, str):
        resolved_level = getattr(logging, level.upper(), logging.INFO)
    else:
        resolved_level = level
    logger = logging.getLogger(name)
    logger.setLevel(resolved_level)
    logger.propagate = False

    resolved_logs_dir = Path(logs_dir)
    resolved_logs_dir.mkdir(parents=True, exist_ok=True)
    file_path = resolved_logs_dir / f"{name}.log"

    if not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(resolved_level)
        stream_handler.setFormatter(_ColorFormatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(stream_handler)

        file_handler = RotatingFileHandler(file_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(file_handler)
    else:
        for handler in logger.handlers:
            handler.setLevel(resolved_level)

    return logger
