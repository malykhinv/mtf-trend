"""Client abstractions for interacting with external exchanges."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol
from uuid import uuid4

from bot import config
from bot.domain.models.exchange import Exchange
from bot.domain.models.signal_direction import SignalDirection


class ExchangeClientError(RuntimeError):
    """Raised when an exchange operation fails or the network is unavailable."""


class OrderStatus(str, Enum):
    """Lifecycle statuses for orders maintained by :class:`ExchangeClient`."""

    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class BracketOrderRequest:
    """Request data required to submit a bracket (entry + stop/take) order."""

    client_trade_id: str
    exchange: Exchange
    symbol: str
    side: SignalDirection
    quantity: float
    entry_price: float
    take_profit_price: float
    stop_loss_price: float


@dataclass(frozen=True, slots=True)
class OrderExecutionSnapshot:
    """Snapshot describing the execution state of a single exchange order."""

    order_id: str
    status: OrderStatus
    filled_qty: float
    avg_fill_price: float | None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BracketOrderExecution:
    """Result of submitting a bracket order to the exchange."""

    entry: OrderExecutionSnapshot
    stop_order_id: str | None
    take_order_id: str | None


class ExchangeClient(Protocol):
    """Interface used by :class:`ExecutionService` to talk to an exchange."""

    def submit_bracket_order(self, request: BracketOrderRequest) -> BracketOrderExecution:
        ...  # pragma: no cover - interface definition

    def fetch_order(self, order_id: str) -> OrderExecutionSnapshot:
        ...  # pragma: no cover - interface definition

    def cancel_order(self, order_id: str) -> OrderExecutionSnapshot:
        ...  # pragma: no cover - interface definition

    def close_position_market(
        self,
        *,
        exchange: Exchange,
        symbol: str,
        side: SignalDirection,
        quantity: float,
        price_hint: float | None = None,
    ) -> OrderExecutionSnapshot:
        ...  # pragma: no cover - interface definition


@dataclass(slots=True)
class InMemoryExchangeClient(ExchangeClient):
    """Simplified exchange client used for tests and local backtests."""

    def __post_init__(self) -> None:
        self._orders: dict[str, OrderExecutionSnapshot] = {}

    def submit_bracket_order(self, request: BracketOrderRequest) -> BracketOrderExecution:
        order_id = f"{request.client_trade_id}-entry-{uuid4().hex[:8]}"
        now = datetime.now(tz=config.TIMEZONE)
        entry_snapshot = OrderExecutionSnapshot(
            order_id=order_id,
            status=OrderStatus.FILLED,
            filled_qty=request.quantity,
            avg_fill_price=request.entry_price,
            updated_at=now,
        )
        self._orders[order_id] = entry_snapshot

        stop_id = f"{request.client_trade_id}-stop-{uuid4().hex[:8]}"
        take_id = f"{request.client_trade_id}-take-{uuid4().hex[:8]}"
        self._orders[stop_id] = OrderExecutionSnapshot(
            order_id=stop_id,
            status=OrderStatus.NEW,
            filled_qty=0.0,
            avg_fill_price=None,
            updated_at=now,
        )
        self._orders[take_id] = OrderExecutionSnapshot(
            order_id=take_id,
            status=OrderStatus.NEW,
            filled_qty=0.0,
            avg_fill_price=None,
            updated_at=now,
        )

        return BracketOrderExecution(entry=entry_snapshot, stop_order_id=stop_id, take_order_id=take_id)

    def fetch_order(self, order_id: str) -> OrderExecutionSnapshot:
        snapshot = self._orders.get(order_id)
        if snapshot is None:
            raise ExchangeClientError(f"Order {order_id} not found")
        return snapshot

    def cancel_order(self, order_id: str) -> OrderExecutionSnapshot:
        snapshot = self.fetch_order(order_id)
        cancelled = OrderExecutionSnapshot(
            order_id=order_id,
            status=OrderStatus.CANCELLED,
            filled_qty=snapshot.filled_qty,
            avg_fill_price=snapshot.avg_fill_price,
            updated_at=datetime.now(tz=config.TIMEZONE),
        )
        self._orders[order_id] = cancelled
        return cancelled

    def close_position_market(
        self,
        *,
        exchange: Exchange,  # noqa: ARG002 - required by the interface
        symbol: str,  # noqa: ARG002 - required by the interface
        side: SignalDirection,  # noqa: ARG002 - required by the interface
        quantity: float,
        price_hint: float | None = None,
    ) -> OrderExecutionSnapshot:
        order_id = f"mkt-close-{uuid4().hex[:8]}"
        snapshot = OrderExecutionSnapshot(
            order_id=order_id,
            status=OrderStatus.FILLED,
            filled_qty=quantity,
            avg_fill_price=price_hint,
            updated_at=datetime.now(tz=config.TIMEZONE),
        )
        self._orders[order_id] = snapshot
        return snapshot
