from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from crypto_screener.config.config import AppConfig as cfg
from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.margin_mode import MarginMode
from crypto_screener.domain.models.order_side import OrderSide
from crypto_screener.domain.models.order_status import OrderStatus
from crypto_screener.domain.models.setup import Buy
from crypto_screener.domain.notifier import Notifier, NotificationType
from crypto_screener.utils.logger import log


@dataclass
class ExecutionResult:
    quantity: float
    entry_order_id: str
    stop_loss_order_id: Optional[str]
    take_profit_order_id: Optional[str]
    partial_close_order_id: Optional[str]
    breakeven_order_id: Optional[str]
    position_id: Optional[str]


class TradeExecutionService:
    def __init__(
            self,
            exchange: Exchange,
            notifier: Notifier,
            max_retries: int = cfg.ORDER_MAX_RETRIES,
            retry_delay_seconds: float = cfg.ORDER_RETRY_DELAY_SECONDS,
    ) -> None:
        self._exchange = exchange
        self._notifier = notifier
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds

    def execute_buy(self, setup: Buy, context: Context) -> Optional[ExecutionResult]:
        try:
            quantity = self._calculate_position_size(setup)
        except Exception as exception:
            self._notify_failure(
                setup,
                context,
                f"Не удалось рассчитать размер позиции: {exception}",
            )
            return None

        entry_order_id: Optional[str] = None
        try:
            entry_order_id = self._place_with_retries(
                label="entry",
                place_order=lambda: self._exchange.place_market_order(
                    setup.symbol,
                    OrderSide.BUY,
                    quantity,
                    margin_mode=MarginMode.CROSS,
                ),
                symbol=setup.symbol,
            )
            protective_order_ids = self._place_protective_orders(setup, quantity)
            position_id = self._fetch_position_id(setup.symbol)
            return ExecutionResult(
                quantity=quantity,
                entry_order_id=entry_order_id,
                position_id=position_id,
                **protective_order_ids,
            )
        except Exception as exception:
            if entry_order_id:
                self._cancel_order_safe(setup.symbol, entry_order_id)
            self._notify_failure(
                setup,
                context,
                f"Не удалось разместить ордера: {exception}",
            )
            return None

    def _calculate_position_size(self, setup: Buy) -> float:
        entry_price = setup.entry_price
        stop_loss_price = setup.stop_loss_price
        stop_distance = entry_price - stop_loss_price
        if stop_distance <= 0:
            raise ValueError(
                f"Некорректная дистанция до стоп-лосса: Entry {entry_price}, SL {stop_loss_price}"
            )

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

    def _place_protective_orders(self, setup: Buy, quantity: float) -> dict[str, Optional[str]]:
        ids: dict[str, Optional[str]] = {
            "stop_loss_order_id": None,
            "take_profit_order_id": None,
            "partial_close_order_id": None,
            "breakeven_order_id": None,
        }

        ids["stop_loss_order_id"] = self._place_with_retries(
            label="stop-loss",
            place_order=lambda: self._exchange.place_stop_loss_order(
                setup.symbol,
                OrderSide.SELL,
                quantity,
                stop_price=setup.stop_loss_price,
                margin_mode=MarginMode.CROSS,
            ),
            symbol=setup.symbol,
        )

        ids["take_profit_order_id"] = self._place_with_retries(
            label="take-profit",
            place_order=lambda: self._exchange.place_take_profit_order(
                setup.symbol,
                OrderSide.SELL,
                quantity,
                price=setup.take_profit_price,
                margin_mode=MarginMode.CROSS,
            ),
            symbol=setup.symbol,
        )

        if setup.partial_close_price is not None:
            partial_quantity = quantity * 0.5
            ids["partial_close_order_id"] = self._place_with_retries(
                label="partial-close",
                place_order=lambda: self._exchange.place_take_profit_order(
                    setup.symbol,
                    OrderSide.SELL,
                    partial_quantity,
                    price=setup.partial_close_price,
                    margin_mode=MarginMode.CROSS,
                ),
                symbol=setup.symbol,
            )

            if setup.breakeven_price is not None:
                ids["breakeven_order_id"] = self._place_with_retries(
                    label="breakeven",
                    place_order=lambda: self._exchange.place_stop_loss_order(
                        setup.symbol,
                        OrderSide.SELL,
                        quantity - partial_quantity,
                        stop_price=setup.breakeven_price,
                        margin_mode=MarginMode.CROSS,
                    ),
                    symbol=setup.symbol,
                )

        return ids

    def _place_with_retries(
            self,
            label: str,
            place_order: Callable[[], str],
            symbol: str,
    ) -> str:
        last_error: Optional[Exception] = None
        for attempt in range(1, self._max_retries + 1):
            try:
                order_id = place_order()
                if self._is_filled(symbol, order_id):
                    return order_id
                log.d(
                    f"Ордер {label} {order_id} для {symbol} не исполнен полностью, попытка {attempt}."
                )
                self._cancel_order_safe(symbol, order_id)
            except Exception as exception:
                last_error = exception
                log.e(
                    f"Ошибка при размещении {label} для {symbol} (попытка {attempt}/{self._max_retries}):"
                    f" {exception}"
                )
            time.sleep(self._retry_delay_seconds)

        raise RuntimeError(
            f"Не удалось разместить {label} для {symbol} после {self._max_retries} попыток: {last_error}"
        )

    def _is_filled(self, symbol: str, order_id: str) -> bool:
        try:
            order_info = self._exchange.get_order_status(symbol, order_id)
        except NotImplementedError:
            return True

        if order_info.status == OrderStatus.FILLED:
            return True
        if order_info.status == OrderStatus.CANCELED:
            raise RuntimeError(f"Ордер {order_id} для {symbol} отменен биржей")
        return False

    def _cancel_order_safe(self, symbol: str, order_id: str) -> None:
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

    def _notify_failure(self, setup: Buy, context: Context, message: str) -> None:
        full_message = (
            f"Ошибка открытия позиции {setup.symbol} на {setup.timeframe.tf}: {message}\n"
            f"Уровни: Entry {setup.entry_price:.4f}, SL {setup.stop_loss_price:.4f}, "
            f"TP {setup.take_profit_price:.4f}"
        )
        log.e(full_message)
        try:
            self._notifier.notify(
                notification_type=NotificationType.ORDER,
                message=full_message,
                context=context,
            )
        except Exception as exception:
            log.e(f"Не удалось отправить уведомление об ошибке сделки: {exception}")
