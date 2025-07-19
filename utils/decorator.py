def inject_method_name(func):
    def wrapper(self, *args, **kwargs):
        setattr(self, '_name', func.__name__)
        try:
            return func(self, *args, **kwargs)
        finally:
            delattr(self, '_name')
    return wrapper
