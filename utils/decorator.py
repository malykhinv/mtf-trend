import time
import threading

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

_depth = threading.local()


def log_duration_ms(func):
    """Декоратор: логирует время выполнения метода в миллисекундах."""
    if not IS_FUNCTION_DURATION_LOG_ENABLED:
        return func

    def wrapper(*args, **kwargs):
        if not hasattr(_depth, "value"):
            _depth.value = 0
        _depth.value += 1
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            indent = "\t" * (_depth.value - 1)
            if elapsed_ms > 200:
                log(f"{indent}{func.__qualname__} : {elapsed_ms} мс")
            _depth.value -= 1
    return wrapper

