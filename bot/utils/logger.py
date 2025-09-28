"""Logging helpers for the trading bot."""
from __future__ import annotations

import logging
from typing import Optional

from bot import config


def setup_logging(name: str = "bot") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(fmt="%(asctime)s %(message)s", datefmt=config.LOG_TIME_FMT)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name if name else "bot")
