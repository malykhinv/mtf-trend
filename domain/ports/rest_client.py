from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RestClient(Protocol):
    """Abstract interface for REST operations."""

    async def get_open_interest(self, symbol: str) -> float:
        """Return the current open interest for ``symbol``."""
        raise NotImplementedError

    async def get_taker_ratio(self, symbol: str) -> tuple[float, float]:
        """Return taker buy/sell ratio for ``symbol`` as a tuple."""
        raise NotImplementedError

    async def get_premium_pct(self, symbol: str) -> float:
        """Return funding premium percentage for ``symbol``."""
        raise NotImplementedError

    async def get_24h_stats(self, symbol: str) -> tuple[float, float]:
        """Return 24h quote volume and last price for ``symbol``."""
        raise NotImplementedError

    async def fetch_all_tickers(self) -> list[tuple[str, float, float]]:
        """Fetch bid/ask data for all tickers."""
        raise NotImplementedError

    async def get_depth(self, symbol: str) -> tuple[tuple[float, float], ...]:
        """Return top of book depth levels for ``symbol``."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release any network resources held by the client."""
        raise NotImplementedError
