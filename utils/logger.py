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


class _ExceptionOnlyFilter(logging.Filter):
    """Пропускает только записи с исключениями и traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        return bool(record.exc_info)


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
    exception_file_path = resolved_logs_dir / f"{name}.exceptions.log"

    if not logger.handlers:
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
        file_handler.set_name("default_file")
        logger.addHandler(file_handler)

        exception_file_handler = RotatingFileHandler(
            exception_file_path,
            maxBytes=LOGGER_FILE_MAX_BYTES,
            backupCount=LOGGER_FILE_BACKUP_COUNT,
            encoding=LOGGER_FILE_ENCODING,
        )
        exception_file_handler.setLevel(logging.ERROR)
        exception_file_handler.setFormatter(logging.Formatter(LOGGER_MESSAGE_FORMAT, datefmt=LOGGER_DATE_FORMAT))
        exception_file_handler.addFilter(_ExceptionOnlyFilter())
        exception_file_handler.set_name("exceptions_file")
        logger.addHandler(exception_file_handler)
    else:
        for handler in logger.handlers:
            if handler.get_name() == "exceptions_file":
                handler.setLevel(logging.ERROR)
            else:
                handler.setLevel(resolved_level)
        has_exception_handler = any(handler.get_name() == "exceptions_file" for handler in logger.handlers)
        if not has_exception_handler:
            exception_file_handler = RotatingFileHandler(
                exception_file_path,
                maxBytes=LOGGER_FILE_MAX_BYTES,
                backupCount=LOGGER_FILE_BACKUP_COUNT,
                encoding=LOGGER_FILE_ENCODING,
            )
            exception_file_handler.setLevel(logging.ERROR)
            exception_file_handler.setFormatter(logging.Formatter(LOGGER_MESSAGE_FORMAT, datefmt=LOGGER_DATE_FORMAT))
            exception_file_handler.addFilter(_ExceptionOnlyFilter())
            exception_file_handler.set_name("exceptions_file")
            logger.addHandler(exception_file_handler)

    setattr(logger, "exception_log_path", str(exception_file_path))

    return logger
