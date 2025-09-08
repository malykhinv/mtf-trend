from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RestClient(Protocol):
    """Abstract interface for REST operations."""

    async def get_open_interest(self, symbol: str) -> float:
        ...

    async def get_taker_ratio(self, symbol: str) -> tuple[float, float]:
        ...

    async def get_premium_pct(self, symbol: str) -> float:
        ...

    async def get_24h_stats(self, symbol: str) -> tuple[float, float]:
        ...

    async def fetch_all_tickers(self) -> list[dict[str, str]]:
        ...

    async def get_depth(self, symbol: str) -> tuple[tuple[float, float], ...]:
        ...
