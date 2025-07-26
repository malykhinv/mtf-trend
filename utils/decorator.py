import time

from config.constants import IS_FUNCTION_DURATION_LOG_ENABLED
from utils.logger import log


def inject_method_name(func):
    """
    Декоратор: инжектирует имя метода в self._name при вызове для последующего логгирования/отладки.
    """
    def wrapper(self, *args, **kwargs):
        """Обёртка: сохраняет имя метода в self._name на время исполнения."""
        setattr(self, '_name', func.__name__)
        try:
            return func(self, *args, **kwargs)
        finally:
            delattr(self, '_name')
    return wrapper

def log_duration_ms(func):
    """Декоратор: логирует время выполнения метода в миллисекундах."""
    if not IS_FUNCTION_DURATION_LOG_ENABLED:
        return func

    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if elapsed_ms > 200:
                log(f"{func.__qualname__} : {elapsed_ms} мс")
    return wrapper

