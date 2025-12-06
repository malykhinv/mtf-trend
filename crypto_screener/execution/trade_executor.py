from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from crypto_screener.config.config import AppConfig as cfg
from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.margin_mode import MarginMode
from crypto_screener.domain.models.order_side import OrderSide
from crypto_screener.domain.models.order_status import OrderStatus
from crypto_screener.domain.models.protective_orders import ProtectiveOrders
from crypto_screener.domain.models.setup import Buy
from crypto_screener.domain.models.stop_realignment_result import StopRealignmentResult
from crypto_screener.domain.notifier import Notifier, NotificationType
from crypto_screener.utils.logger import log


@dataclass
class ExecutionResult:
    quantity: float
    entry_order_id: str
    protective_orders: ProtectiveOrders
    position_id: Optional[str]


class TradeExecutionService:
    def __init__(
            self,
            exchange: Exchange,
            notifier: Notifier,
    ) -> None:
        self._exchange = exchange
        self.notifier = notifier

    def execute_buy(self, setup: Buy, context: Context) -> Optional[ExecutionResult]:
        entry_order_id: Optional[str] = None
        protective_orders = ProtectiveOrders()
        try:
            quantity = self._determine_position_size(setup, context)
        except Exception:
            return None

        try:
            entry_order_id = self._place_entry_order(setup, quantity)
            protective_orders = self._place_protective_bundle(setup, quantity)
            position_id = self._extract_position_id(setup.symbol)
            return ExecutionResult(
                quantity=quantity,
                entry_order_id=entry_order_id,
                protective_orders=protective_orders,
                position_id=position_id,
            )
        except Exception as exception:
            self._handle_execution_failure(
                setup, context, entry_order_id, protective_orders, exception
            )
            return None

    def _determine_position_size(
            self,
            setup: Buy,
            context: Context
    ) -> float:
        try:
            return self._calculate_position_size(setup)
        except Exception as exception:
            self._notify_failure(
                setup,
                context,
                f"Не удалось рассчитать размер позиции: {exception}",
            )
            raise

    def _place_entry_order(
            self,
            setup: Buy,
            quantity: float
    ) -> str:
        order_id, _ = self._place_with_retries(
            label="entry",
            place_order=lambda: self._exchange.place_market_order(
                setup.symbol,
                OrderSide.BUY,
                quantity,
                margin_mode=MarginMode.CROSS,
            ),
            symbol=setup.symbol,
            acceptable_statuses={
                OrderStatus.FILLED,
                OrderStatus.NEW,
                OrderStatus.PARTIALLY_FILLED,
            },
        )
        return order_id

    def _place_protective_bundle(
            self,
            setup: Buy,
            quantity: float,
    ) -> ProtectiveOrders:
        return self._place_protective_orders(setup, quantity)

    def _extract_position_id(
            self,
            symbol: str
    ) -> Optional[str]:
        return self._fetch_position_id(symbol)

    def _handle_execution_failure(
            self,
            setup: Buy,
            context: Context,
            entry_order_id: Optional[str],
            protective_orders: ProtectiveOrders,
            exception: Exception,
    ) -> None:
        self.cancel_order_safe(setup.symbol, entry_order_id)
        for order_id in (
                protective_orders.stop_loss_id,
                protective_orders.take_profit_id,
                protective_orders.partial_close_id,
                protective_orders.breakeven_id,
        ):
            self.cancel_order_safe(setup.symbol, order_id)
        self._notify_failure(
            setup,
            context,
            f"Не удалось разместить ордера: {exception}",
        )

    @staticmethod
    def _calculate_position_size(setup: Buy) -> float:
        entry_price = setup.entry_price
        stop_loss_price = setup.stop_loss_price
        stop_distance = entry_price - stop_loss_price
        if stop_distance <= 0:
            raise ValueError(f"Некорректная дистанция до стоп-лосса: Entry {entry_price}, SL {stop_loss_price}")

        risk_amount = cfg.RISK_PER_TRADE_USDT
        min_notional = cfg.MIN_POSITION_NOTIONAL_USDT
        min_quantity = cfg.MIN_POSITION_QUANTITY

        quantity_by_risk = risk_amount / stop_distance
        quantity_by_notional = min_notional / entry_price
        quantity = max(quantity_by_risk, quantity_by_notional, min_quantity)

        log.d(
            f"Рассчитан размер позиции: {quantity:.6f} ({quantity * entry_price:.2f} USDT) "
            f"при риске {risk_amount} USDT и SL-расстоянии {stop_distance:.4f}."
        )
        return quantity

    def _place_protective_orders(
            self,
            setup: Buy,
            quantity: float,
            orders: Optional[ProtectiveOrders] = None,
    ) -> ProtectiveOrders:
        orders = orders or ProtectiveOrders()

        orders = self._place_stop_loss(setup, quantity, orders)
        orders = self.place_take_profit(setup, quantity, orders)
        return self.place_partial_close(setup, quantity, orders)

    def _place_stop_loss(
            self,
            setup: Buy,
            quantity: float,
            orders: ProtectiveOrders,
    ) -> ProtectiveOrders:
        orders.stop_loss_id, orders.stop_loss_status = self._place_with_retries(
            label="stop-loss",
            place_order=lambda: self._exchange.place_stop_loss_order(
                setup.symbol,
                OrderSide.SELL,
                quantity,
                stop_price=setup.stop_loss_price,
                margin_mode=MarginMode.CROSS,
            ),
            symbol=setup.symbol,
            acceptable_statuses=self._protective_acceptable_statuses(),
        )
        return orders

    def place_take_profit(
            self,
            setup: Buy,
            quantity: float,
            orders: ProtectiveOrders,
    ) -> ProtectiveOrders:
        orders.take_profit_id, orders.take_profit_status = self._place_with_retries(
            label="take-profit",
            place_order=lambda: self._exchange.place_take_profit_order(
                setup.symbol,
                OrderSide.SELL,
                quantity,
                price=setup.take_profit_price,
                margin_mode=MarginMode.CROSS,
            ),
            symbol=setup.symbol,
            acceptable_statuses=self._protective_acceptable_statuses(),
        )
        return orders

    def place_partial_close(
            self,
            setup: Buy,
            quantity: float,
            orders: ProtectiveOrders,
    ) -> ProtectiveOrders:
        if setup.partial_close_price is None:
            return orders

        partial_quantity = quantity * 0.5
        orders.partial_close_id, orders.partial_close_status = self._place_with_retries(
            label="partial-close",
            place_order=lambda: self._exchange.place_take_profit_order(
                setup.symbol,
                OrderSide.SELL,
                partial_quantity,
                price=setup.partial_close_price,
                margin_mode=MarginMode.CROSS,
            ),
            symbol=setup.symbol,
            acceptable_statuses=self._protective_acceptable_statuses(),
        )
        return orders

    @staticmethod
    def _protective_acceptable_statuses() -> set[OrderStatus]:
        return {
            OrderStatus.FILLED,
            OrderStatus.NEW,
            OrderStatus.PARTIALLY_FILLED,
        }

    def _place_with_retries(
            self,
            label: str,
            place_order: Callable[[], str],
            symbol: str,
            acceptable_statuses: Optional[set[OrderStatus]] = None,
    ) -> tuple[str, Optional[OrderStatus]]:
        acceptable_statuses = acceptable_statuses or {OrderStatus.FILLED}
        try:
            order_id = place_order()
            status = self._get_order_status(symbol, order_id)
            if status is None or status in acceptable_statuses:
                if status and status != OrderStatus.FILLED:
                    log.d(f"Ордер {label} {order_id} для {symbol} имеет статус {status.value}.")
                return order_id, status
            if status == OrderStatus.CANCELED:
                raise RuntimeError(f"Ордер {order_id} для {symbol} отменен биржей")
            log.d(f"Ордер {label} {order_id} для {symbol} имеет статус {status.value}, отменяем ордер.")
            self.cancel_order_safe(symbol, order_id)
            raise RuntimeError(
                f"Ордер {label} {order_id} для {symbol} имеет недопустимый статус {status.value}"
            )
        except Exception as exception:
            log.e(f"Ошибка при размещении {label} для {symbol}: {exception}")
            raise

    def _get_order_status(
            self,
            symbol: str,
            order_id: str
    ) -> Optional[OrderStatus]:
        try:
            order_info = self._exchange.get_order_status(symbol, order_id)
        except NotImplementedError:
            return None

        return order_info.status

    def realign_stop_orders(
            self,
            setup: Buy,
            stop_loss_order_id: Optional[str],
            breakeven_order_id: Optional[str],
            remaining_quantity: float,
            move_to_breakeven: bool,
            stop_loss_status: Optional[OrderStatus] = None,
            breakeven_status: Optional[OrderStatus] = None,
    ) -> StopRealignmentResult:
        current_ids = self._current_stop_orders_result(
            setup.symbol,
            stop_loss_order_id,
            breakeven_order_id,
            stop_loss_status,
            breakeven_status,
        )

        early_exit_result = self._handle_no_remaining_quantity(
            setup, stop_loss_order_id, breakeven_order_id, remaining_quantity
        )
        if early_exit_result:
            return early_exit_result

        stop_price, label, is_breakeven_target = self._target_stop_order_details(
            setup, move_to_breakeven
        )
        new_stop_id, new_stop_status = self._place_realigned_stop(
            setup=setup,
            remaining_quantity=remaining_quantity,
            stop_price=stop_price,
            label=label,
        )
        if new_stop_id is None:
            return current_ids

        self._cancel_previous_stops(
            setup.symbol, stop_loss_order_id, breakeven_order_id
        )
        return self._collect_stop_realign_result(
            new_stop_id, new_stop_status, is_breakeven_target
        )

    def _current_stop_orders_result(
            self,
            symbol: str,
            stop_loss_order_id: Optional[str],
            breakeven_order_id: Optional[str],
            stop_loss_status: Optional[OrderStatus],
            breakeven_status: Optional[OrderStatus],
    ) -> StopRealignmentResult:
        stop_loss_status = stop_loss_status or (
            self._get_order_status(symbol, stop_loss_order_id)
            if stop_loss_order_id
            else None
        )
        breakeven_status = breakeven_status or (
            self._get_order_status(symbol, breakeven_order_id)
            if breakeven_order_id
            else None
        )
        return StopRealignmentResult(
            stop_loss_id=stop_loss_order_id,
            breakeven_id=breakeven_order_id,
            stop_loss_status=stop_loss_status,
            breakeven_status=breakeven_status,
        )

    def _handle_no_remaining_quantity(
            self,
            setup: Buy,
            stop_loss_order_id: Optional[str],
            breakeven_order_id: Optional[str],
            remaining_quantity: float,
    ) -> Optional[StopRealignmentResult]:
        if remaining_quantity > 0:
            return None

        log.d(f"Нет оставшегося объема для перестановки стоп-ордера по {setup.symbol} после частичного закрытия.")
        self._cancel_previous_stops(
            setup.symbol, stop_loss_order_id, breakeven_order_id
        )
        return StopRealignmentResult()

    @staticmethod
    def _target_stop_order_details(
            setup: Buy,
            move_to_breakeven: bool
    ) -> tuple[float, str, bool]:
        move_to_breakeven_target = (
                move_to_breakeven and setup.breakeven_price is not None
        )
        stop_price = (
            setup.breakeven_price if move_to_breakeven_target else setup.stop_loss_price
        )
        label = "breakeven" if move_to_breakeven_target else "stop-loss"
        return stop_price, label, move_to_breakeven_target

    def _place_realigned_stop(
            self,
            setup: Buy,
            remaining_quantity: float,
            stop_price: float,
            label: str,
    ) -> tuple[Optional[str], Optional[OrderStatus]]:
        try:
            order_id, status = self._place_with_retries(
                label=label,
                place_order=lambda: self._exchange.place_stop_loss_order(
                    setup.symbol,
                    OrderSide.SELL,
                    remaining_quantity,
                    stop_price=stop_price,
                    margin_mode=MarginMode.CROSS,
                ),
                symbol=setup.symbol,
                acceptable_statuses={
                    OrderStatus.FILLED,
                    OrderStatus.NEW,
                    OrderStatus.PARTIALLY_FILLED,
                },
            )
            return order_id, status
        except Exception as exception:
            log.e(f"Не удалось переставить {label} для {setup.symbol}: {exception}")
            return None, None

    def _cancel_previous_stops(
            self,
            symbol: str,
            stop_loss_order_id: Optional[str],
            breakeven_order_id: Optional[str],
    ) -> None:
        self.cancel_order_safe(symbol, stop_loss_order_id)
        self.cancel_order_safe(symbol, breakeven_order_id)

    @staticmethod
    def _collect_stop_realign_result(
            new_stop_id: str,
            new_stop_status: Optional[OrderStatus],
            is_breakeven_target: bool,
    ) -> StopRealignmentResult:
        result = StopRealignmentResult()
        if is_breakeven_target:
            result.breakeven_id = new_stop_id
            result.breakeven_status = new_stop_status or OrderStatus.NEW
        else:
            result.stop_loss_id = new_stop_id
            result.stop_loss_status = new_stop_status or OrderStatus.NEW
        return result

    def cancel_order_safe(self, symbol: str, order_id: Optional[str]) -> None:
        if not order_id:
            return
        try:
            self._exchange.cancel_order(symbol, order_id)
        except NotImplementedError:
            log.d(f"Отмена ордера {order_id} для {symbol} не поддерживается биржей.")
        except Exception as exception:
            log.e(f"Не удалось отменить ордер {order_id} для {symbol}: {exception}")

    def _fetch_position_id(self, symbol: str) -> Optional[str]:
        try:
            position = self._exchange.get_position(symbol)
            return position.symbol if position else None
        except NotImplementedError:
            return None
        except Exception as exception:
            log.e(f"Не удалось получить позицию {symbol}: {exception}")
            return None

    def _notify_failure(
            self,
            setup: Buy,
            context: Context,
            message: str
    ) -> None:
        full_message = (
            f"Ошибка открытия позиции {setup.symbol} на {setup.timeframe.tf}: {message}\n"
            f"Уровни: Entry {setup.entry_price:.4f}, SL {setup.stop_loss_price:.4f}, "
            f"TP {setup.take_profit_price:.4f}"
        )
        log.e(full_message)
        try:
            self.notifier.notify(
                notification_type=NotificationType.ORDER,
                message=full_message,
                context=context,
            )
        except Exception as exception:
            log.e(f"Не удалось отправить уведомление об ошибке сделки: {exception}")
