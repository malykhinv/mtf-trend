from __future__ import annotations

import logging


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("mtf-trend")
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("Логирование настроено")
    return logger


logger = configure_logging()

