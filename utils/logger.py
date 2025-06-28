import logging
import sys

logging.basicConfig(
    stream=sys.stdout,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO
)

def log(message: str) -> None:
    logging.info(f"    {message}")

def logw(message: str) -> None:
    logging.info(f"  ✕ {message}")