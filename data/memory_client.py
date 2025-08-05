from __future__ import annotations

from typing import Sequence

from domain.bar import Bar
from domain.timeframe import Timeframe

from .exchange_client import ExchangeClient


class MemoryExchangeClient(ExchangeClient):
    """Простой клиент, возвращающий заранее подготовленные данные."""

    def __init__(self, bars: dict[str, Sequence[Bar]], market_caps: dict[str, float]):
        self._bars = bars
        self._market_caps = market_caps

    async def fetch_bars(self, symbol: str, timeframe: Timeframe, limit: int) -> Sequence[Bar]:
        return self._bars.get(symbol, [])[-limit:]

    async def fetch_market_caps(self) -> dict[str, float]:
        return self._market_caps

