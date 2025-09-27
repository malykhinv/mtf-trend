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


class PositionStatus(str, Enum):
    """High-level status of a symbol position returned by the exchange."""

    NONE = "NONE"
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class SymbolPositionSnapshot:
    """Snapshot describing position and linked orders for a symbol."""

    exchange: Exchange
    symbol: str
    status: PositionStatus
    side: SignalDirection | None
    quantity: float
    entry: OrderExecutionSnapshot | None
    stop: OrderExecutionSnapshot | None
    take: OrderExecutionSnapshot | None
    close: OrderExecutionSnapshot | None
    updated_at: datetime | None = None


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

    def fetch_position(
        self,
        *,
        exchange: Exchange,
        symbol: str,
    ) -> SymbolPositionSnapshot:
        ...  # pragma: no cover - interface definition


@dataclass(slots=True)
class InMemoryExchangeClient(ExchangeClient):
    """Simplified exchange client used for tests and local backtests."""

    def __post_init__(self) -> None:
        self._orders: dict[str, OrderExecutionSnapshot] = {}
        self._positions: dict[tuple[str, str], dict[str, object]] = {}
        self._order_to_symbol: dict[str, tuple[str, str]] = {}

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
        stop_snapshot = OrderExecutionSnapshot(
            order_id=stop_id,
            status=OrderStatus.NEW,
            filled_qty=0.0,
            avg_fill_price=None,
            updated_at=now,
        )
        take_snapshot = OrderExecutionSnapshot(
            order_id=take_id,
            status=OrderStatus.NEW,
            filled_qty=0.0,
            avg_fill_price=None,
            updated_at=now,
        )
        self._orders[stop_id] = stop_snapshot
        self._orders[take_id] = take_snapshot

        key = (request.exchange.value, request.symbol)
        self._positions[key] = {
            "status": PositionStatus.OPEN,
            "side": request.side,
            "quantity": request.quantity,
            "entry_id": order_id,
            "stop_id": stop_id,
            "take_id": take_id,
            "close_id": None,
            "updated_at": now,
        }
        for oid in (order_id, stop_id, take_id):
            self._order_to_symbol[oid] = key

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
        self._update_position_for_order(order_id, cancelled)
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
        key = (exchange.value, symbol)
        position = self._positions.setdefault(
            key,
            {
                "status": PositionStatus.CLOSED,
                "side": side,
                "quantity": 0.0,
                "entry_id": None,
                "stop_id": None,
                "take_id": None,
                "close_id": None,
                "updated_at": snapshot.updated_at,
            },
        )
        position["close_id"] = order_id
        position["status"] = PositionStatus.CLOSED
        position["quantity"] = 0.0
        position["updated_at"] = snapshot.updated_at
        if "side" not in position or position["side"] is None:
            position["side"] = side
        self._order_to_symbol[order_id] = key
        return snapshot

    def fetch_position(
        self,
        *,
        exchange: Exchange,
        symbol: str,
    ) -> SymbolPositionSnapshot:
        key = (exchange.value, symbol)
        position = self._positions.get(key)
        if position is None:
            return SymbolPositionSnapshot(
                exchange=exchange,
                symbol=symbol,
                status=PositionStatus.NONE,
                side=None,
                quantity=0.0,
                entry=None,
                stop=None,
                take=None,
                close=None,
            )

        entry_id = position.get("entry_id")
        stop_id = position.get("stop_id")
        take_id = position.get("take_id")
        close_id = position.get("close_id")

        entry_snapshot = self._orders.get(entry_id) if isinstance(entry_id, str) else None
        stop_snapshot = self._orders.get(stop_id) if isinstance(stop_id, str) else None
        take_snapshot = self._orders.get(take_id) if isinstance(take_id, str) else None
        close_snapshot = self._orders.get(close_id) if isinstance(close_id, str) else None

        status: PositionStatus = position.get("status", PositionStatus.NONE)  # type: ignore[assignment]
        if status is not PositionStatus.CLOSED:
            if stop_snapshot and stop_snapshot.status is OrderStatus.FILLED:
                status = PositionStatus.CLOSED
            elif take_snapshot and take_snapshot.status is OrderStatus.FILLED:
                status = PositionStatus.CLOSED
            elif close_snapshot and close_snapshot.status is OrderStatus.FILLED:
                status = PositionStatus.CLOSED
            elif entry_snapshot and entry_snapshot.status is OrderStatus.CANCELLED:
                status = PositionStatus.NONE

        timestamps = [position.get("updated_at")]
        for snapshot in (entry_snapshot, stop_snapshot, take_snapshot, close_snapshot):
            if snapshot and snapshot.updated_at is not None:
                timestamps.append(snapshot.updated_at)
        updated_at = max((ts for ts in timestamps if isinstance(ts, datetime)), default=None)

        position["status"] = status
        position["updated_at"] = updated_at

        return SymbolPositionSnapshot(
            exchange=exchange,
            symbol=symbol,
            status=status,
            side=position.get("side"),  # type: ignore[arg-type]
            quantity=float(position.get("quantity", 0.0)),
            entry=entry_snapshot,
            stop=stop_snapshot,
            take=take_snapshot,
            close=close_snapshot,
            updated_at=updated_at,
        )

    def _update_position_for_order(
        self, order_id: str, snapshot: OrderExecutionSnapshot
    ) -> None:
        key = self._order_to_symbol.get(order_id)
        if key is None:
            return
        position = self._positions.get(key)
        if position is None:
            return
        position["updated_at"] = snapshot.updated_at
        if order_id == position.get("entry_id") and snapshot.status is OrderStatus.CANCELLED:
            position["status"] = PositionStatus.NONE
            position["quantity"] = 0.0
        if order_id == position.get("close_id") and snapshot.status is OrderStatus.FILLED:
            position["status"] = PositionStatus.CLOSED
