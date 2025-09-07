from __future__ import annotations

from typing import Protocol

from domain.models.market_data import AggTrade, DepthSnapshot, LiquidationEvent


class WsClient(Protocol):
    """Abstract interface for websocket client."""

    def subscribe_symbols(self, symbols: tuple[str, ...]) -> None:
        ...

    async def stream(self) -> None:  # pragma: no cover - network
        ...

    def next_agg_trade(self) -> AggTrade | None:
        ...

    def next_depth(self) -> DepthSnapshot | None:
        ...

    def next_liquidation(self) -> LiquidationEvent | None:
        ...
