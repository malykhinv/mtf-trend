"""Модуль проекта."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataClient(ABC):
    """Класс."""
    @abstractmethod
    def get_market_cap(self, symbol: str) -> float:
        """Метод."""
    @abstractmethod
    def get_top_coins_by_market_cap(self, limit: int) -> list[str]:
        """Метод."""
    @abstractmethod
    def get_total_volumes(self, symbols_or_coin_ids: list[str]) -> dict[str, float]:
        """Метод."""