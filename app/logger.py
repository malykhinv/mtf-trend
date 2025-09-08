import logging
from typing import Optional

_LOG_FORMAT = ':< %(message)s :>'


def configure(level: int = logging.INFO) -> None:
    """Configure the root logger.

    Parameters
    ----------
    level: int
        Logging level. Defaults to ``logging.INFO`` but can be
        overridden from configuration.
    """
    logging.basicConfig(level=level, format=_LOG_FORMAT, force=True)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a logger with the given name."""
    return logging.getLogger(name)


__all__ = ['configure', 'get_logger']
