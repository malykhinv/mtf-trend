from __future__ import annotations

from typing import Protocol, Any

from domain.models.trading import OrderSpec


class Trader(Protocol):
    """Abstract trading interface."""

    async def place(self, order: OrderSpec) -> Any | None:  # pragma: no cover - network
        """Place ``order`` on the exchange."""
        raise NotImplementedError

    async def cancel(
        self, symbol: str, order_id: int | None
    ) -> None:  # pragma: no cover - network
        """Cancel an order by ``order_id`` or all orders for ``symbol``."""
        raise NotImplementedError
