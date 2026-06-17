"""Доменный слой: исключения."""


class ExchangeConnectivityError(RuntimeError):
    """Ошибка сетевой доступности биржи."""


class ExchangeOrderNotFound(RuntimeError):
    """Биржа ответила, что запрошенный ордер не существует. Это не сетевая ошибка."""
