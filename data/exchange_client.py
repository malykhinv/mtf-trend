from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from domain.bar import Bar
from domain.timeframe import Timeframe


class ExchangeClient(ABC):
    """Абстрактный клиент биржи."""

    @abstractmethod
    async def fetch_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> Sequence[Bar]:
        """Получить свечи для символа."""

    @abstractmethod
    async def fetch_market_caps(self) -> dict[str, float]:
        """Получить капитализации монет."""

