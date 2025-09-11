from __future__ import annotations

from typing import Protocol

from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent


class WsClient(Protocol):
    """Abstract interface for websocket client."""

    def subscribe_symbols(self, symbols: tuple[str, ...]) -> None:
        """Subscribe to updates for ``symbols``."""
        raise NotImplementedError

    def add_detail_streams(self, symbols: tuple[str, ...]) -> None:
        """Subscribe to depth and liquidation streams for ``symbols``."""
        raise NotImplementedError

    def remove_detail_streams(self, symbols: tuple[str, ...]) -> None:
        """Unsubscribe from depth and liquidation streams for ``symbols``."""
        raise NotImplementedError

    async def stream(self) -> None:  # pragma: no cover - network
        """Start streaming websocket data."""
        raise NotImplementedError

    async def next_agg_trade(self) -> AggTrade | None:
        """Return the next aggregated trade event."""
        raise NotImplementedError

    async def next_depth(self) -> DepthSnapshot | None:
        """Return the next order book depth snapshot."""
        raise NotImplementedError

    async def next_liquidation(self) -> LiquidationEvent | None:
        """Return the next liquidation event."""
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - network
        """Close the websocket connection."""
        raise NotImplementedError
