"""No-op market data client used when external market-cap providers are disabled."""

from __future__ import annotations

from domain.abstract.market_data_client import MarketDataClient


class NoOpMarketDataClient(MarketDataClient):
    def get_market_cap(self, symbol: str) -> float:
        return 0.0

    def get_top_coins_by_market_cap(self, limit: int) -> list[str]:
        return []

    def get_market_caps(self, symbols_or_coin_ids: list[str]) -> dict[str, float]:
        return {}

    def get_total_volumes(self, symbols_or_coin_ids: list[str]) -> dict[str, float]:
        return {}
