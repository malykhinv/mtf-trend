import logging
from typing import Optional


def get_logger(name: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger(name or "app")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
    logger.i = lambda msg, *a, **k: logger.info(msg, *a, **k)
    logger.e = lambda msg, *a, **k: logger.error(msg, *a, **k)
    logger.d = lambda msg, *a, **k: logger.debug(msg, *a, **k)
    return logger


log = get_logger(__name__)
