import time

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

    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if elapsed_ms > 200:
                name = func.__qualname__
                log(f"{name} : {elapsed_ms} мс")

    return wrapper
