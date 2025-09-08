from __future__ import annotations

from typing import Protocol

from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent


class WsClient(Protocol):
    """Abstract interface for websocket client."""

    def subscribe_symbols(self, symbols: tuple[str, ...]) -> None:
        ...

    async def stream(self) -> None:  # pragma: no cover - network
        ...

    async def next_agg_trade(self) -> AggTrade | None:
        ...

    async def next_depth(self) -> DepthSnapshot | None:
        ...

    async def next_liquidation(self) -> LiquidationEvent | None:
        ...

    async def close(self) -> None:  # pragma: no cover - network
        ...
