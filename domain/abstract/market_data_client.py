"""Market capitalization provider abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataClient(ABC):
    """Interface for market-cap data providers."""

    @abstractmethod
    def get_market_cap(self, symbol: str) -> float:
        """Return market capitalization for one symbol."""

    @abstractmethod
    def get_top_coins_by_market_cap(self, limit: int) -> list[str]:
        """Return top coins sorted by market capitalization."""

    @abstractmethod
    def get_total_volumes(self, symbols_or_coin_ids: list[str]) -> dict[str, float]:
        """Return 24h total trading volumes in USD for input symbols/coin IDs."""
