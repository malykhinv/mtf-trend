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


class _RuntimeNoiseFilter(logging.Filter):
    """Нормализует runtime-логи до человекочитаемого backtest-формата."""

    _REWRITE_SUBSTRINGS: Final[tuple[tuple[str, int, str], ...]] = (
        ("pno diagnostics export start", logging.WARNING, "Диагностика PNO: экспорт артефактов"),
        ("pno diagnostics export done", logging.WARNING, "Диагностика PNO завершена"),
        ("pno diagnostics export finished", logging.WARNING, "Диагностика PNO завершена"),
        ("pno diagnostics export complete", logging.WARNING, "Диагностика PNO завершена"),
        ("pno diagnostics export end", logging.WARNING, "Диагностика PNO завершена"),
        ("pno diagnostics export", logging.WARNING, "Диагностика PNO"),
        ("trade charts render start", logging.WARNING, "Графики: генерация"),
        ("trade charts export start", logging.WARNING, "Графики: генерация"),
        ("pno trade charts render start", logging.WARNING, "Графики: генерация"),
        ("pno trade charts export start", logging.WARNING, "Графики: генерация"),
        ("render trade charts start", logging.WARNING, "Графики: генерация"),
        ("export trade charts start", logging.WARNING, "Графики: генерация"),
        ("генерация графиков", logging.WARNING, "Графики: генерация"),
        ("экспорт графиков", logging.WARNING, "Графики: генерация"),
        ("trade charts render done", logging.WARNING, "Графики готовы"),
        ("trade charts export done", logging.WARNING, "Графики готовы"),
        ("pno trade charts render done", logging.WARNING, "Графики готовы"),
        ("pno trade charts export done", logging.WARNING, "Графики готовы"),
        ("render trade charts done", logging.WARNING, "Графики готовы"),
        ("export trade charts done", logging.WARNING, "Графики готовы"),
        ("графики готовы", logging.WARNING, "Графики готовы"),
    )

    _ALLOWED_PREFIXES: Final[tuple[str, ...]] = (
        "запуск бектеста",
        "запуск бэктеста",
        "отобрано ",
        "таймфреймы:",
        "анализ ",
        "сделок нет",
        "сделок:",
        "винрейт:",
        "profit factor:",
        "pnl:",
        "max dd:",
        "sl:",
        "tp1_be:",
        "tp2:",
        "диагностика pno",
        "графики",
        "прогон остановлен",
    )

    _ALLOWED_SUBSTRINGS: Final[tuple[str, ...]] = (
        "проверено:",
        "eta:",
        "не хватило памяти",
    )

    _SUPPRESSED_SUBSTRINGS: Final[tuple[str, ...]] = (
        "run-backtest: старт",
        "run-backtest: output_root=",
        "запуск-бэктеста:",
        "pre-rank",
        "анализ-кэша:",
        "подготовка-символов",
        "сводка по символам",
        "В работу ушло",
        "тяжёлый прогон",
        "тяжелый прогон",
        "Почему рынок не пустил во вход",
        "Бэктест начинает",
        "Дошёл до символа",
        "Глава ",
        "Сетка продвинулась",
        "Финал бэктеста",
        "run-backtest: трейдерская сводка:",
        "run-backtest: по сетке:",
        "run-backtest: runner final close:",
        "лучшая комбинация дала",
        "по сетке: комбинаций=",
        "runner final close:",
        "Категории получили отдельные CSV",
        "PNO-артефакты разложены по категориям",
        "plot=false",
        "plot=true",
        "symbols=",
        "stages=",
        "комбинаций=",
        "прибыльных=",
        "со сделками=",
        "средний трейд",
        "лучшая комбинация",
        "исходы:",
    )

    @staticmethod
    def _extract_int_after(message: str, prefix: str) -> int | None:
        lowered = message.lower()
        prefix_lowered = prefix.lower()
        start = lowered.find(prefix_lowered)
        if start < 0:
            return None
        value_start = start + len(prefix_lowered)
        value_end = value_start
        while value_end < len(message) and message[value_end].isdigit():
            value_end += 1
        if value_end == value_start:
            return None
        return int(message[value_start:value_end])

    @classmethod
    def _normalize_message_for_matching(cls, message: str) -> str:
        normalized = message.strip()
        if normalized.lower().startswith("run-backtest: "):
            normalized = normalized.split(": ", 1)[1].strip()
        return normalized

    @classmethod
    def _is_allowed_runtime_message(cls, message: str) -> bool:
        normalized = cls._normalize_message_for_matching(message).lower()
        if normalized.startswith(cls._ALLOWED_PREFIXES):
            return True
        return any(fragment in normalized for fragment in cls._ALLOWED_SUBSTRINGS)

    @classmethod
    def _format_rewritten_message(cls, message: str, base_message: str) -> str:
        symbols_count = cls._extract_int_after(message, "symbols=")
        if symbols_count is not None:
            return f"{base_message}: {symbols_count} символов"
        charts_count = cls._extract_int_after(message, "charts=")
        if charts_count is not None:
            return f"{base_message}: {charts_count} графиков"
        trades_count = cls._extract_int_after(message, "trades=")
        if trades_count is not None:
            return f"{base_message}: {trades_count} сделок"
        return base_message

    @classmethod
    def _rewrite_record(cls, record: logging.LogRecord, message: str) -> bool:
        lowered = message.lower()
        for fragment, levelno, base_message in cls._REWRITE_SUBSTRINGS:
            if fragment in lowered:
                record.msg = cls._format_rewritten_message(message, base_message)
                record.args = ()
                record.levelno = levelno
                record.levelname = logging.getLevelName(levelno)
                return True
        return False

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()

        if record.levelno >= logging.ERROR:
            return True

        if self._rewrite_record(record, message):
            return True

        lowered = message.lower()
        if any(fragment.lower() in lowered for fragment in self._SUPPRESSED_SUBSTRINGS):
            return False

        if record.levelno in {logging.INFO, logging.WARNING}:
            return self._is_allowed_runtime_message(message)

        return True


def _ensure_runtime_noise_filter(handler: logging.Handler) -> None:
    if any(isinstance(existing_filter, _RuntimeNoiseFilter) for existing_filter in handler.filters):
        return
    handler.addFilter(_RuntimeNoiseFilter())


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

    if not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(resolved_level)
        stream_handler.setFormatter(_ColorFormatter(LOGGER_MESSAGE_FORMAT, datefmt=LOGGER_DATE_FORMAT))
        _ensure_runtime_noise_filter(stream_handler)
        logger.addHandler(stream_handler)

        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=LOGGER_FILE_MAX_BYTES,
            backupCount=LOGGER_FILE_BACKUP_COUNT,
            encoding=LOGGER_FILE_ENCODING,
        )
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(logging.Formatter(LOGGER_MESSAGE_FORMAT, datefmt=LOGGER_DATE_FORMAT))
        _ensure_runtime_noise_filter(file_handler)
        logger.addHandler(file_handler)
    else:
        for handler in logger.handlers:
            handler.setLevel(resolved_level)
            _ensure_runtime_noise_filter(handler)

    return logger
