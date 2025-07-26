import logging
import sys

logging.basicConfig(
    stream=sys.stdout,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO
)

def log(message: str) -> None:
    """Печатает информационное сообщение."""
    logging.info(f"    {message}")

def logw(message: str) -> None:
    """Печатает предупреждение."""
    logging.info(f"  ✕ {message}")