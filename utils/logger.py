# utils/logger.py
import logging

logging.basicConfig(
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO
)

def log(message: str) -> None:
    logging.info(message)