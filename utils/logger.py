import logging
import sys

class FixedWidthFormatter(logging.Formatter):
    def format(self, record):
        record.threadName = f"{record.threadName:<24}"
        return super().format(record)

handler = logging.StreamHandler(sys.stdout)
formatter = FixedWidthFormatter(
    fmt="%(asctime)s %(message)s",
    datefmt="%H:%M:%S"
)
handler.setFormatter(formatter)

logging.basicConfig(level=logging.INFO, handlers=[handler])

def log(message: str) -> None:
    """Печатает информационное сообщение."""
    logging.info(f"  {message}")

def logw(message: str) -> None:
    """Печатает предупреждение."""
    logging.info(f"✕ {message}")