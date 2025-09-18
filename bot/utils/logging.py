from __future__ import annotations

import logging
from logging import Logger
from typing import Optional


_LOGGER_CACHE: dict[str, Logger] = {}


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def get_logger(name: str, level: Optional[str] = None) -> Logger:
    if name in _LOGGER_CACHE:
        return _LOGGER_CACHE[name]
    logger = logging.getLogger(name)
    if level:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    _LOGGER_CACHE[name] = logger
    return logger
