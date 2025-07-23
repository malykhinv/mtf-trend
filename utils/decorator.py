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
