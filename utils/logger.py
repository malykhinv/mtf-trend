"""Утилиты логирования с компактным выводом и временными метками."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from constants import (
    LOGGER_COLOR_ERROR,
    LOGGER_COLOR_INFO,
    LOGGER_COLOR_RESET,
    LOGGER_COLOR_WARNING,
    LOGGER_DATE_FORMAT,
    LOGGER_FILE_BACKUP_COUNT,
    LOGGER_FILE_ENCODING,
    LOGGER_FILE_MAX_BYTES,
    LOGGER_MESSAGE_FORMAT,
    DEFAULT_LOGS_DIR,
)


class _ColorFormatter(logging.Formatter):
    """Класс."""
    RESET = LOGGER_COLOR_RESET
    COLORS = {
        logging.INFO: LOGGER_COLOR_INFO,
        logging.WARNING: LOGGER_COLOR_WARNING,
        logging.ERROR: LOGGER_COLOR_ERROR,
        logging.CRITICAL: LOGGER_COLOR_ERROR,
    }

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        color = self.COLORS.get(record.levelno)
        if color is None:
            return message
        return f"{color}{message}{self.RESET}"


_NAMED_LOG_LEVELS: Final[dict[str, int]] = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def get_logger(
    name: str,
    level: int | str = logging.INFO,
    logs_dir: str | Path = DEFAULT_LOGS_DIR,
    *,
    console_output: bool = True,
) -> logging.Logger:
    """Создает или возвращает настроенный логгер в формате `ЧЧ:ММ:СС Сообщение`."""
    if isinstance(level, str):
        resolved_level = _NAMED_LOG_LEVELS.get(level.upper(), logging.INFO)
    else:
        resolved_level = level
    logger = logging.getLogger(name)
    logger.setLevel(resolved_level)
    logger.propagate = False

    resolved_logs_dir = Path(logs_dir)
    resolved_logs_dir.mkdir(parents=True, exist_ok=True)
    file_path = resolved_logs_dir / f"{name}.log"

    if not logger.handlers:
        if console_output:
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(resolved_level)
            stream_handler.setFormatter(_ColorFormatter(LOGGER_MESSAGE_FORMAT, datefmt=LOGGER_DATE_FORMAT))
            logger.addHandler(stream_handler)

        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=LOGGER_FILE_MAX_BYTES,
            backupCount=LOGGER_FILE_BACKUP_COUNT,
            encoding=LOGGER_FILE_ENCODING,
        )
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(logging.Formatter(LOGGER_MESSAGE_FORMAT, datefmt=LOGGER_DATE_FORMAT))
        logger.addHandler(file_handler)
    else:
        has_file_handler = False
        has_stream_handler = False
        for handler in logger.handlers:
            if isinstance(handler, RotatingFileHandler):
                has_file_handler = True
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                has_stream_handler = True
                handler.setLevel(resolved_level if console_output else logging.CRITICAL + 1)
            else:
                handler.setLevel(resolved_level)
        if console_output and not has_stream_handler:
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(resolved_level)
            stream_handler.setFormatter(_ColorFormatter(LOGGER_MESSAGE_FORMAT, datefmt=LOGGER_DATE_FORMAT))
            logger.addHandler(stream_handler)
        if not has_file_handler:
            file_handler = RotatingFileHandler(
                file_path,
                maxBytes=LOGGER_FILE_MAX_BYTES,
                backupCount=LOGGER_FILE_BACKUP_COUNT,
                encoding=LOGGER_FILE_ENCODING,
            )
            file_handler.setLevel(resolved_level)
            file_handler.setFormatter(logging.Formatter(LOGGER_MESSAGE_FORMAT, datefmt=LOGGER_DATE_FORMAT))
            logger.addHandler(file_handler)

    return logger
