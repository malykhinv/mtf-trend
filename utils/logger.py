# utils/logger.py
from datetime import datetime

def log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"{now} {message}")
