from __future__ import annotations

from abc import ABC, abstractmethod
from domain.models.bar import Bar
from domain.models.timeframe import Timeframe


class ExchangeClient(ABC):
    """Клиент биржи."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def api_secret(self) -> str:
        return self._api_secret

    @abstractmethod
    async def fetch_symbols(self) -> list[str]:
        """Получить символы."""

    @abstractmethod
    async def fetch_bars(self, symbol: str, timeframe: Timeframe, limit: int = 500) -> list[Bar]:
        """Получить свечи для символа."""
