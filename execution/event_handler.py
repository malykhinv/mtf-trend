from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from domain.models import ActiveOrder, OrderRole
from infrastructure import (
    Notifier,
    get_logger,
    log_cooldown_started,
    log_entry_canceled,
    log_order_error,
    log_scenario_cancelled,
    log_stop_loss,
    log_take_profit,
)
from state import SymbolState

from .order_manager import OrderManager


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True)
class OrderUpdateEvent:
    """Normalized order update event emitted by the exchange."""

    symbol: str
    order_id: str
    status: str
    filled: float
    remaining: float
    average_price: Optional[float] = None
    timestamp: Optional[datetime] = None


@dataclass
class ExecutionEventHandler:
    """React to order execution events and update symbol state."""

    order_manager: OrderManager
    cooldown_minutes: int
    now_factory: Callable[[], datetime] = field(default_factory=lambda: _utc_now)
    notifier: Notifier | None = None
    logger: logging.Logger = field(default_factory=lambda: get_logger(__name__))

    def handle_order_update(self, state: SymbolState, event: OrderUpdateEvent) -> SymbolState:
        role, active_order = self._find_active_order(state, event.order_id)
        if active_order is None:
            self.logger.debug("Получено событие по неизвестному ордеру %s", event.order_id)
            return state

        delta = active_order.register_fill(event.filled)
        status = event.status.lower()
        event_time = event.timestamp or self.now_factory()
        fill_price = event.average_price

        if role == OrderRole.ENTRY and delta > 0:
            state.open_quantity += delta
            state.has_open_position = state.open_quantity > 0
            state.mark_active()
        elif role in {OrderRole.STOP_LOSS, OrderRole.TAKE_PROFIT_1, OrderRole.TAKE_PROFIT_2} and delta > 0:
            state.reduce_position(delta)

        if role == OrderRole.TAKE_PROFIT_1 and status == "closed":
            self._handle_take_profit_one(
                symbol=event.symbol,
                state=state,
                event_time=event_time,
                fill_price=fill_price,
            )
        elif role in {OrderRole.STOP_LOSS, OrderRole.TAKE_PROFIT_2} and status == "closed":
            self._handle_position_closed(
                symbol=event.symbol,
                state=state,
                completed_role=role,
                event_time=event_time,
                fill_price=fill_price,
            )

        if status in {"closed", "canceled"}:
            state.active_orders.pop(role, None)

        if role == OrderRole.ENTRY and status == "canceled":
            self._handle_entry_canceled(symbol=event.symbol, state=state)

        state.has_open_position = state.open_quantity > 0.0
        if not state.has_open_position and role != OrderRole.ENTRY and status == "closed":
            state.mark_idle()

        return state

    def handle_scenario_cancelled(self, state: SymbolState, symbol: str, reason: str | None = None) -> SymbolState:
        if state.active_orders:
            self.order_manager.cancel_orders(symbol, [order.order_id for order in state.active_orders.values()])
        state.reset_orders()
        state.reset_position()
        state.mark_idle()
        log_scenario_cancelled(
            logger=self.logger,
            notifier=self.notifier,
            symbol=symbol,
            reason=reason,
        )
        return state

    def handle_order_error(self, state: SymbolState, symbol: str, error: Exception) -> SymbolState:
        cooldown_until = self.now_factory() + timedelta(minutes=self.cooldown_minutes)
        log_order_error(logger=self.logger, notifier=self.notifier, symbol=symbol, error=error)
        state.mark_cooldown(cooldown_until)
        state.reset_orders()
        state.reset_position()
        log_cooldown_started(
            logger=self.logger,
            notifier=self.notifier,
            symbol=symbol,
            until=cooldown_until,
        )
        return state

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _find_active_order(self, state: SymbolState, order_id: str) -> tuple[OrderRole | None, ActiveOrder | None]:
        for role, active in state.active_orders.items():
            if active.order_id == order_id:
                return role, active
        return None, None

    def _handle_take_profit_one(
        self,
        symbol: str,
        state: SymbolState,
        *,
        event_time: datetime,
        fill_price: float | None,
    ) -> None:
        params = state.last_order_params
        stop_order = state.active_orders.get(OrderRole.STOP_LOSS)
        if params is None or stop_order is None:
            return
        remaining_quantity = max(state.open_quantity, 0.0)
        if remaining_quantity <= 0:
            return
        break_even_price = (params.entry_price + params.take_profit_1) / 2.0
        try:
            new_stop_id, new_stop_price = self.order_manager.replace_stop_loss(
                symbol,
                stop_order.order_id,
                quantity=remaining_quantity,
                new_stop_price=break_even_price,
            )
        except Exception as exc:  # pragma: no cover - network interaction
            self.logger.error("Не удалось перенести стоп по %s в безубыток: %s", symbol, exc)
            return
        state.active_orders[OrderRole.STOP_LOSS] = ActiveOrder(order_id=new_stop_id, quantity=remaining_quantity)
        executed_price = fill_price if fill_price is not None else params.take_profit_1
        log_take_profit(
            logger=self.logger,
            notifier=self.notifier,
            symbol=symbol,
            stage="TP1",
            price=executed_price,
            timestamp=event_time,
            new_stop=new_stop_price,
        )

    def _handle_position_closed(
        self,
        symbol: str,
        state: SymbolState,
        *,
        completed_role: OrderRole,
        event_time: datetime,
        fill_price: float | None,
    ) -> None:
        to_cancel = [
            order.order_id
            for role, order in state.active_orders.items()
            if role != completed_role
        ]
        if to_cancel:
            self.order_manager.cancel_orders(symbol, to_cancel)
        params = state.last_order_params
        state.reset_orders()
        state.reset_position()
        state.mark_idle()

        if completed_role == OrderRole.TAKE_PROFIT_2:
            price = fill_price if fill_price is not None else (params.take_profit_2 if params else 0.0)
            log_take_profit(
                logger=self.logger,
                notifier=self.notifier,
                symbol=symbol,
                stage="TP2",
                price=price,
                timestamp=event_time,
            )
        elif completed_role == OrderRole.STOP_LOSS:
            price = fill_price if fill_price is not None else (params.stop_loss if params else 0.0)
            log_stop_loss(
                logger=self.logger,
                notifier=self.notifier,
                symbol=symbol,
                price=price,
                timestamp=event_time,
            )

    def _handle_entry_canceled(self, symbol: str, state: SymbolState) -> None:
        to_cancel = [
            order.order_id
            for role, order in state.active_orders.items()
            if role != OrderRole.ENTRY
        ]
        if to_cancel:
            self.order_manager.cancel_orders(symbol, to_cancel)
        state.reset_orders()
        state.reset_position()
        state.mark_idle()
        log_entry_canceled(
            logger=self.logger,
            notifier=self.notifier,
            symbol=symbol,
        )


__all__ = ["ExecutionEventHandler", "OrderUpdateEvent"]
