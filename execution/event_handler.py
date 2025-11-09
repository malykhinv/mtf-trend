from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from domain.models import ActiveOrder, OrderRole, ScenarioStatus, SymbolState

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
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def handle_order_update(self, state: SymbolState, event: OrderUpdateEvent) -> SymbolState:
        role, active_order = self._find_active_order(state, event.order_id)
        if active_order is None:
            self.logger.debug("Получено событие по неизвестному ордеру %s", event.order_id)
            return state

        delta = active_order.register_fill(event.filled)
        status = event.status.lower()

        if role == OrderRole.ENTRY and delta > 0:
            state.open_quantity += delta
            state.has_open_position = state.open_quantity > 0
            state.status = ScenarioStatus.ACTIVE
        elif role in {OrderRole.STOP_LOSS, OrderRole.TAKE_PROFIT_1, OrderRole.TAKE_PROFIT_2} and delta > 0:
            state.open_quantity = max(state.open_quantity - delta, 0.0)

        if role == OrderRole.TAKE_PROFIT_1 and status == "closed":
            self._handle_take_profit_one(symbol=event.symbol, state=state)
        elif role in {OrderRole.STOP_LOSS, OrderRole.TAKE_PROFIT_2} and status == "closed":
            self._handle_position_closed(symbol=event.symbol, state=state, completed_role=role)

        if status in {"closed", "canceled"}:
            state.active_orders.pop(role, None)

        if role == OrderRole.ENTRY and status == "canceled":
            self._handle_entry_canceled(symbol=event.symbol, state=state)

        state.has_open_position = state.open_quantity > 0.0
        if not state.has_open_position and role != OrderRole.ENTRY and status == "closed":
            state.status = ScenarioStatus.IDLE

        return state

    def handle_scenario_cancelled(self, state: SymbolState, symbol: str, reason: str | None = None) -> SymbolState:
        if state.active_orders:
            self.order_manager.cancel_orders(symbol, [order.order_id for order in state.active_orders.values()])
            state.active_orders.clear()
        state.open_quantity = 0.0
        state.has_open_position = False
        state.last_order_params = None
        state.status = ScenarioStatus.IDLE
        self.logger.info("Сценарий по %s отменён%s", symbol, f": {reason}" if reason else "")
        return state

    def handle_order_error(self, state: SymbolState, symbol: str, error: Exception) -> SymbolState:
        self.logger.error("Ошибка размещения ордеров по %s: %s", symbol, error)
        cooldown_until = self.now_factory() + timedelta(minutes=self.cooldown_minutes)
        state.cooldown_until = cooldown_until
        state.status = ScenarioStatus.COOLDOWN
        state.active_orders.clear()
        state.open_quantity = 0.0
        state.has_open_position = False
        state.last_order_params = None
        return state

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _find_active_order(self, state: SymbolState, order_id: str) -> tuple[OrderRole | None, ActiveOrder | None]:
        for role, active in state.active_orders.items():
            if active.order_id == order_id:
                return role, active
        return None, None

    def _handle_take_profit_one(self, symbol: str, state: SymbolState) -> None:
        params = state.last_order_params
        stop_order = state.active_orders.get(OrderRole.STOP_LOSS)
        if params is None or stop_order is None:
            return
        remaining_quantity = max(state.open_quantity, 0.0)
        if remaining_quantity <= 0:
            return
        break_even_price = (params.entry_price + params.take_profit_1) / 2.0
        try:
            new_stop_id = self.order_manager.replace_stop_loss(
                symbol,
                stop_order.order_id,
                quantity=remaining_quantity,
                new_stop_price=break_even_price,
            )
        except Exception as exc:  # pragma: no cover - network interaction
            self.logger.error("Не удалось перенести стоп по %s в безубыток: %s", symbol, exc)
            return
        state.active_orders[OrderRole.STOP_LOSS] = ActiveOrder(order_id=new_stop_id, quantity=remaining_quantity)

    def _handle_position_closed(
        self,
        symbol: str,
        state: SymbolState,
        *,
        completed_role: OrderRole,
    ) -> None:
        to_cancel = [
            order.order_id
            for role, order in state.active_orders.items()
            if role != completed_role
        ]
        if to_cancel:
            self.order_manager.cancel_orders(symbol, to_cancel)
        state.active_orders.clear()
        state.open_quantity = 0.0
        state.has_open_position = False
        state.last_order_params = None
        state.status = ScenarioStatus.IDLE

    def _handle_entry_canceled(self, symbol: str, state: SymbolState) -> None:
        to_cancel = [
            order.order_id
            for role, order in state.active_orders.items()
            if role != OrderRole.ENTRY
        ]
        if to_cancel:
            self.order_manager.cancel_orders(symbol, to_cancel)
        state.active_orders.clear()
        state.open_quantity = 0.0
        state.has_open_position = False
        state.last_order_params = None
        state.status = ScenarioStatus.IDLE


__all__ = ["ExecutionEventHandler", "OrderUpdateEvent"]
