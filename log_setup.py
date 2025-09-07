import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from constants import LOG_DIR, LOG_MAX_BYTES, LOG_BACKUP_COUNT


class _ExactLevelFilter(logging.Filter):
    def __init__(self, level: int) -> None:
        super().__init__()
        self._level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == self._level


def setup_logging() -> None:
    """Configure application logging with rotation."""
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    info_handler = RotatingFileHandler(
        Path(LOG_DIR) / "info.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
    )
    info_handler.setLevel(logging.INFO)
    info_handler.addFilter(_ExactLevelFilter(logging.INFO))
    info_handler.setFormatter(fmt)

    debug_handler = RotatingFileHandler(
        Path(LOG_DIR) / "debug.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.addFilter(_ExactLevelFilter(logging.DEBUG))
    debug_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(info_handler)
    root.addHandler(debug_handler)
