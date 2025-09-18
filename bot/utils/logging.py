from __future__ import annotations

import logging
from logging import Logger
from typing import Optional


_LEVEL_BY_NAME: dict[str, int] = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


_LOGGER_CACHE: dict[str, Logger] = {}


def _resolve_level(level: str) -> int:
    return _LEVEL_BY_NAME.get(level.upper(), logging.INFO)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=_resolve_level(level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def get_logger(name: str, level: Optional[str] = None) -> Logger:
    if name in _LOGGER_CACHE:
        return _LOGGER_CACHE[name]
    logger = logging.getLogger(name)
    if level:
        logger.setLevel(_resolve_level(level))
    _LOGGER_CACHE[name] = logger
    return logger
