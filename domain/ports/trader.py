from __future__ import annotations

from typing import Protocol

from domain.models.trading import OrderSpec


class Trader(Protocol):
    """Abstract trading interface."""

    async def place(self, order: OrderSpec) -> None:  # pragma: no cover - network
        ...

    async def cancel(
        self, symbol: str, order_id: int | None
    ) -> None:  # pragma: no cover - network
        ...
