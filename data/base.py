from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Sequence

from domain.bar import Bar


class ExchangeClient(ABC):
    """Абстрактный клиент биржи."""

    @abstractmethod
    async def fetch_bars(self, symbol: str, timeframe: str, limit: int) -> Sequence[Bar]:
        """Получить свечи для символа."""

    @abstractmethod
    async def fetch_market_caps(self) -> dict[str, float]:
        """Получить капитализации монет."""

