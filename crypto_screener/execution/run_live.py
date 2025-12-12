from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from heapq import heapify, heappop, heappush
from time import sleep
from typing import Optional
from urllib.parse import quote

from crypto_screener.config.config import AppConfig as cfg
from crypto_screener.data.notifiers.telegram import SKIP_CALLBACK_PREFIX, TRADE_CALLBACK_PREFIX
from crypto_screener.data.providers.coingecko import enrich_symbols_capitalization
from crypto_screener.domain.capture_state import CaptureState
from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.active_trade import ActiveTrade
from crypto_screener.domain.models.active_trade_registry import ActiveTradeKey, ActiveTrades
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.capture_registry import CaptureKey
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.order_status import OrderStatus
from crypto_screener.domain.models.position import Position
from crypto_screener.domain.models.protective_order_statuses import ProtectiveOrderStatuses
from crypto_screener.domain.models.protective_orders import ProtectiveOrders
from crypto_screener.domain.models.setup import Capture, Setup, Trade, Unfilled
from crypto_screener.domain.models.symbol import FuturesSymbol, set_contexts
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.models.trade_result import TradeResult
from crypto_screener.domain.notifier import Keyboard, Notifier, NotificationType
from crypto_screener.domain.strategies.strategy import Strategy
from crypto_screener.execution.trade_executor import TradeExecutionService
from crypto_screener.execution.trade_permission_service import TradePermissionService
from crypto_screener.utils.telegram_messages import (
    build_capture_message,
    build_protective_recovery_message,
    build_trade_closure_message,
    build_trade_opened_message,
)
from crypto_screener.utils.history import calculate_limit_grid
from crypto_screener.utils.logger import log
from crypto_screener.utils.plotter import plot, plot_postmortem
from crypto_screener.utils.signals import handle_sig
from crypto_screener.utils.time import utc_now

TIMEFRAME_INTERVALS: dict[Timeframe, timedelta] = {
    timeframe: timedelta(minutes=timeframe.minutes)
    for timeframe in Timeframe
}


@dataclass(order=True)
class ScheduledTask:
    next_run_at: datetime
    symbol: FuturesSymbol = field(compare=False)
    timeframe: Timeframe = field(compare=False)

# region Private.
def _fetch_filtered_symbols(
        exchange: Exchange,
        listing_period_days: int,
        volume_24h_new_usdt_min: int,
        volume_24h_old_usdt_min: int,
        trades_24h_min: int,
        trades_24h_btc_ratio_min: float
) -> list[FuturesSymbol]:
    symbols = exchange.get_futures_symbols()
    symbols = enrich_symbols_capitalization(symbols)
    symbols = set_contexts(symbols, listing_period_days)
    btc_trades_24h = next((symbol.trades_24h for symbol in symbols if symbol.symbol.startswith("BTC")), 0)
    filtered_symbols = _filter_symbols(
        symbols,
        listing_period_days,
        volume_24h_new_usdt_min,
        volume_24h_old_usdt_min,
        trades_24h_min,
        trades_24h_btc_ratio_min,
        btc_trades_24h
    )
    log.i(f"Отобрано {len(filtered_symbols)} символов из {len(symbols)}.")
    return filtered_symbols


def _filter_symbols(
        symbols: list[FuturesSymbol],
        listing_period_days: int,
        volume_24h_new_usdt_min: int,
        volume_24h_old_usdt_min: int,
        trades_24h_min: int,
        trades_24h_btc_ratio_min: float,
        btc_trades_24h: int
) -> list[FuturesSymbol]:
    filtered: list[FuturesSymbol] = []

    for symbol in symbols:
        listing_age_days = (utc_now() - symbol.listing_time).days
        if listing_age_days <= listing_period_days:
            if symbol.volume_usdt_24h < volume_24h_new_usdt_min:
                continue
            filtered.append(symbol)
        else:
            if symbol.volume_usdt_24h < volume_24h_old_usdt_min:
                continue
            if symbol.trades_24h < min(trades_24h_min, int(trades_24h_btc_ratio_min * btc_trades_24h)):
                continue
            filtered.append(symbol)
    return filtered


def _build_keyboard(
        symbol: str,
        timeframe: Timeframe,
        context: Context
) -> Keyboard:
    trade_text = "Торговать"
    skip_text = "Пропустить"
    encoded_symbol = quote(symbol, safe="")
    encoded_context = quote(context.value, safe="")
    callback_data = f"{TRADE_CALLBACK_PREFIX}:{encoded_symbol}:{timeframe.tf}:{encoded_context}"
    skip_callback_data = f"{SKIP_CALLBACK_PREFIX}:{encoded_symbol}:{timeframe.tf}:{encoded_context}"
    return [[(trade_text, callback_data), (skip_text, skip_callback_data)]]


def _send_notification(
        notifier: Notifier,
        notification_type: NotificationType,
        message: str,
        setup: Setup,
        subdir: Optional[str] = None,
        context: Optional[Context] = None,
        keyboard: Optional[Keyboard] = None,
) -> Optional[str]:
    try:
        image_path = plot(
            setup=setup,
            subdir=subdir,
            context=context,
        )
        return notifier.notify(
            notification_type,
            message,
            image_path,
            keyboard=keyboard,
            context=context,
        )
    except Exception as exception:
        log.e(f"Ошибка при отправке уведомления: {exception}")
    return None


def _edit_notification(
        notifier: Notifier,
        notification_type: NotificationType,
        message_id: str,
        message: str,
        setup: Setup,
        subdir: Optional[str] = None,
        context: Optional[Context] = None,
        keyboard: Optional[Keyboard] = None,
) -> Optional[str]:
    try:
        image_path = plot(
            setup=setup,
            subdir=subdir,
            context=context,
        )
        return notifier.edit_message(
            notification_type=notification_type,
            message_id=message_id,
            message=message,
            image_path=image_path,
            keyboard=keyboard,
            context=context,
        )
    except Exception as exception:
        log.e(f"Ошибка при редактировании уведомления: {exception}")
    return None


def _remove_button(
        notifier: Notifier,
        message_id: Optional[str]
) -> None:
    try:
        notifier.remove_button(message_id)
    except Exception as exception:
        log.e(f"Ошибка при удалении кнопки {message_id}: {exception}")


def _fetch_order_status(
        exchange: Exchange,
        symbol: str,
        order_id: Optional[str]
):
    if not order_id:
        return None
    try:
        return exchange.get_order_status(symbol, order_id)
    except NotImplementedError:
        log.d(f"Биржа не поддерживает получение статуса ордера {order_id} по {symbol}.")
    except Exception as exception:
        log.e(f"Не удалось получить статус ордера {order_id} по {symbol}: {exception}")
    return None


def _cancel_order(
        exchange: Exchange,
        symbol: str,
        order_id: Optional[str]
) -> None:
    if not order_id:
        return
    try:
        exchange.cancel_order(symbol, order_id)
        log.i(f"Отменен ордер {order_id} по {symbol}.")
    except NotImplementedError:
        log.d(f"Биржа не поддерживает отмену ордера {order_id} по {symbol}.")
    except Exception as exception:
        log.e(f"Не удалось отменить ордер {order_id} по {symbol}: {exception}")


def _log_status_change(
        label: str,
        previous: Optional[str],
        current: Optional[str],
        order_id: Optional[str]
) -> None:
    if previous == current or current is None:
        return
    log.i(f"Статус {label} ордера {order_id} изменился: {previous} → {current}.")


def _summarize_exit_levels(
        trade_result: TradeResult,
        trade_levels
) -> tuple[str, list[float]]:
    exit_prices: list[float] = []
    label = trade_result.value
    match trade_result:
        case TradeResult.SL:
            exit_prices = [trade_levels.stop_loss_price]
            label = "SL"
        case TradeResult.TP:
            exit_prices = [trade_levels.take_profit_price]
            label = "TP"
        case TradeResult.PC_BE:
            exit_prices = [
                price
                for price in (trade_levels.partial_close_price, trade_levels.breakeven_price)
                if price is not None
            ]
            label = "PC → BE"
        case TradeResult.PC_TP:
            exit_prices = [
                price
                for price in (trade_levels.partial_close_price, trade_levels.take_profit_price)
                if price is not None
            ]
            label = "PC → TP"
    return label, exit_prices


def _notify_trade_closure(
        notifier: Notifier,
        active_trade: ActiveTrade,
        trade_result: TradeResult,
        profit_pct: float,
        profit_value: float,
        exit_prices: list[float],
        exchange_name: str,
) -> None:
    setup = active_trade.setup
    trade_levels = setup.trade_levels
    exit_label, expected_prices = _summarize_exit_levels(trade_result, trade_levels)
    actual_exit_prices = exit_prices or expected_prices
    actual_entry_price = active_trade.entry_average_price or trade_levels.entry_price

    message = build_trade_closure_message(
        active_trade=active_trade,
        trade_result=trade_result,
        profit_pct=profit_pct,
        profit_value=profit_value,
        exit_label=exit_label,
        exit_prices=actual_exit_prices,
        actual_entry_price=actual_entry_price,
        exchange_name=exchange_name,
    )
    log.i(message)

    try:
        postmortem_path = plot_postmortem(
            setup=setup,
            detection_time=active_trade.detection_time,
            subdir='order',
            postmortem_bars=active_trade.postmortem_bars,
            context=active_trade.context,
        )
    except Exception as exception:
        log.e(f"Ошибка при построении postmortem графика: {exception}")
        postmortem_path = None

    _remove_button(notifier, active_trade.capture_message_id)

    try:
        notifier.notify(
            notification_type=NotificationType.ORDER,
            message=message,
            image_path=postmortem_path,
            context=active_trade.context,
        )
    except Exception as exception:
        log.e(f"Ошибка при отправке уведомления о закрытии сделки: {exception}")


def _update_postmortem_bars(
        active_trade: ActiveTrade,
        new_bars: list[Bar]
) -> list[Bar]:
    existing_times = {bar.time for bar in active_trade.postmortem_bars}
    fresh_bars = [
        bar for bar in new_bars
        if bar.time > active_trade.detection_time and bar.time not in existing_times
    ]
    if fresh_bars:
        active_trade.postmortem_bars.extend(fresh_bars)
        active_trade.postmortem_bars.sort(key=lambda bar: bar.time)
    return active_trade.postmortem_bars


def _add_exit_price(
        exit_prices: list[float],
        price: Optional[float]
) -> None:
    if price is not None:
        exit_prices.append(price)


def _calculate_profit(
        entry_price: float,
        exit_price: float,
        quantity: float
) -> tuple[float, float]:
    profit_value = (exit_price - entry_price) * quantity
    base = entry_price * quantity if entry_price and quantity else 1
    profit_pct = profit_value / base * 100
    return profit_value, profit_pct


def _calculate_split_profit(
        entry_price: float,
        total_quantity: float,
        partial_exit_price: float,
        partial_quantity: float,
        remainder_exit_price: float,
        remainder_quantity: float,
) -> tuple[float, float]:
    partial_profit = (partial_exit_price - entry_price) * partial_quantity if partial_quantity else 0.0
    remainder_profit = (remainder_exit_price - entry_price) * remainder_quantity if remainder_quantity else 0.0
    profit_value = partial_profit + remainder_profit
    base_quantity = total_quantity or partial_quantity + remainder_quantity or 1
    base = entry_price * base_quantity if entry_price else 1
    profit_pct = profit_value / base * 100
    return profit_value, profit_pct


def _get_remaining_quantity(
        context: _TradeMonitorContext,
        active_trade: ActiveTrade
) -> Optional[float]:
    return context.remaining_quantity if context.remaining_quantity is not None else active_trade.quantity



def _notify_protective_recovery(
        trade_execution_service: TradeExecutionService,
        active_trade: ActiveTrade,
        reason: str,
        exchange_name: str,
) -> None:
    log.w(reason)
    message = build_protective_recovery_message(
        active_trade=active_trade,
        reason=reason,
        exchange_name=exchange_name,
    )
    try:
        trade_execution_service.notifier.notify(
            notification_type=NotificationType.ORDER,
            message=message,
            context=active_trade.context,
        )
    except Exception as exception:
        log.e(f"Не удалось отправить уведомление о восстановлении защиты: {exception}")


def _restore_stop_orders(
        trade_execution_service: TradeExecutionService,
        active_trade: ActiveTrade,
        context: _TradeMonitorContext,
        protective_order_statuses: ProtectiveOrderStatuses,
        to_breakeven: bool,
) -> None:
    previous_stop_loss_id = context.protective_orders.stop_loss_id
    previous_breakeven_id = context.protective_orders.breakeven_id
    remaining_quantity = _get_remaining_quantity(context, active_trade) or 0
    realigned_ids = trade_execution_service.realign_stop_orders(
        setup=active_trade.setup,
        stop_loss_order_id=context.protective_orders.stop_loss_id,
        breakeven_order_id=context.protective_orders.breakeven_id,
        stop_loss_status=context.protective_orders.stop_loss_status,
        breakeven_status=context.protective_orders.breakeven_status,
        remaining_quantity=remaining_quantity,
        move_to_breakeven=to_breakeven,
    )

    log.i(
        "Восстанавливаем стоп-ордера после отмены: "
        f"SL {previous_stop_loss_id} → {realigned_ids.stop_loss_id}, "
        f"BE {previous_breakeven_id} → {realigned_ids.breakeven_id}"
    )

    if realigned_ids.stop_loss_id is not None:
        context.protective_orders.stop_loss_id = realigned_ids.stop_loss_id
        context.protective_orders.stop_loss_status = realigned_ids.stop_loss_status or OrderStatus.NEW
        protective_order_statuses.stop_loss = None
        protective_order_statuses.stop_loss_status = context.protective_orders.stop_loss_status

    if realigned_ids.breakeven_id is not None:
        context.protective_orders.breakeven_id = realigned_ids.breakeven_id
        context.protective_orders.breakeven_status = realigned_ids.breakeven_status or OrderStatus.NEW
        protective_order_statuses.breakeven = None
        protective_order_statuses.breakeven_status = context.protective_orders.breakeven_status


def _restore_limit_orders(
        trade_execution_service: TradeExecutionService,
        active_trade: ActiveTrade,
        context: _TradeMonitorContext,
        protective_order_statuses: ProtectiveOrderStatuses,
        order_type: str,
) -> None:
    remaining_quantity = _get_remaining_quantity(context, active_trade) or 0
    if remaining_quantity <= 0:
        log.w(f"Пропускаем восстановление {order_type} по {active_trade.symbol}: нет доступного объема.")
        return

    try:
        if order_type == "take-profit":
            restored_orders = trade_execution_service.place_take_profit(
                setup=active_trade.setup,
                quantity=remaining_quantity,
                orders=context.protective_orders,
            )
            context.protective_orders.take_profit_id = restored_orders.take_profit_id
            context.protective_orders.take_profit_status = restored_orders.take_profit_status
            protective_order_statuses.take_profit = None
            protective_order_statuses.take_profit_status = restored_orders.take_profit_status
        elif order_type == "partial-close" and active_trade.setup.partial_close_price is not None:
            restored_orders = trade_execution_service.place_partial_close(
                setup=active_trade.setup,
                quantity=remaining_quantity,
                orders=context.protective_orders,
            )
            context.protective_orders.partial_close_id = restored_orders.partial_close_id
            context.protective_orders.partial_close_status = restored_orders.partial_close_status
            protective_order_statuses.partial_close = None
            protective_order_statuses.partial_close_status = restored_orders.partial_close_status
    except Exception as exception:
        log.e(f"Не удалось восстановить {order_type} по {active_trade.symbol}: {exception}")


@dataclass
class _TradeMonitorContext:
    entry_price: float
    remaining_quantity: Optional[float]
    protective_orders: ProtectiveOrders


def _build_trade_monitor_context(active_trade: ActiveTrade) -> _TradeMonitorContext:
    protective_orders = active_trade.protective_orders
    protective_orders_copy = ProtectiveOrders(
        stop_loss_id=protective_orders.stop_loss_id,
        stop_loss_status=protective_orders.stop_loss_status,
        take_profit_id=protective_orders.take_profit_id,
        take_profit_status=protective_orders.take_profit_status,
        partial_close_id=protective_orders.partial_close_id,
        partial_close_status=protective_orders.partial_close_status,
        breakeven_id=protective_orders.breakeven_id,
        breakeven_status=protective_orders.breakeven_status,
    )

    return _TradeMonitorContext(
        entry_price=active_trade.entry_average_price or active_trade.setup.entry_price,
        remaining_quantity=active_trade.remaining_quantity,
        protective_orders=protective_orders_copy,
    )


def _update_entry_status(
        exchange: Exchange,
        trade_execution_service: TradeExecutionService,
        active_trade: ActiveTrade,
) -> tuple[Optional[Position], Optional[tuple[TradeResult, float, float, list[float]]]]:
    position: Optional[Position] = None
    protective_orders = active_trade.protective_orders

    entry_info = _fetch_order_status(exchange, active_trade.symbol, active_trade.entry_order_id)
    if entry_info:
        _log_status_change(
            "entry",
            active_trade.entry_order_status and active_trade.entry_order_status.value,
            entry_info.status.value,
            entry_info.id,
        )
        active_trade.entry_order_status = entry_info.status
        active_trade.entry_average_price = entry_info.average_price or active_trade.entry_average_price
        active_trade.quantity = entry_info.quantity or active_trade.quantity
        active_trade.status = entry_info.status

    if active_trade.entry_order_status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
        if active_trade.entry_order_status in (OrderStatus.NEW, OrderStatus.CANCELED):
            log.i(
                f"Статус входного ордера {active_trade.entry_order_id} —"
                f" {active_trade.entry_order_status.value if active_trade.entry_order_status else 'неизвестно'},"
                " пробуем определить позицию через exchange.get_position."
            )

        try:
            position = exchange.get_position(active_trade.symbol)
        except NotImplementedError:
            position = None
        except Exception as exception:
            log.e(f"Не удалось получить позицию {active_trade.symbol}: {exception}")
            position = None

        if not position or position.entry_price is None or position.quantity is None:
            if active_trade.entry_order_status in (OrderStatus.NEW, OrderStatus.CANCELED):
                log.i(
                    f"Входной ордер {active_trade.entry_order_id} в статусе "
                    f"{active_trade.entry_order_status.value if active_trade.entry_order_status else 'неизвестно'} "
                    "и позиция не найдена — завершаем сделку и отменяем связанные ордера."
                )
                trade_execution_service.cancel_order_safe(
                    active_trade.symbol, protective_orders.stop_loss_id
                )
                trade_execution_service.cancel_order_safe(
                    active_trade.symbol, protective_orders.take_profit_id
                )
                trade_execution_service.cancel_order_safe(
                    active_trade.symbol, protective_orders.partial_close_id
                )
                trade_execution_service.cancel_order_safe(
                    active_trade.symbol, protective_orders.breakeven_id
                )
                active_trade.status = OrderStatus.CANCELED
                return position, (TradeResult.MANUAL, 0.0, 0.0, [])

            return position, None

        active_trade.entry_order_status = OrderStatus.FILLED
        active_trade.status = OrderStatus.FILLED
        active_trade.entry_average_price = position.entry_price or active_trade.entry_average_price
        active_trade.quantity = position.quantity or active_trade.quantity

    return position, None


def _refresh_protective_orders(
        exchange: Exchange,
        trade_execution_service: TradeExecutionService,
        active_trade: ActiveTrade,
        context: _TradeMonitorContext,
        exchange_name: str,
) -> ProtectiveOrderStatuses:
    protective_orders = context.protective_orders
    protective_order_statuses = ProtectiveOrderStatuses()

    take_profit_info = _fetch_order_status(exchange, active_trade.symbol, protective_orders.take_profit_id)
    if take_profit_info:
        _log_status_change(
            "take-profit",
            protective_orders.take_profit_status and protective_orders.take_profit_status.value,
            take_profit_info.status.value,
            take_profit_info.id,
        )
        protective_orders.take_profit_status = take_profit_info.status
        protective_order_statuses.take_profit = take_profit_info
        protective_order_statuses.take_profit_status = take_profit_info.status
        if take_profit_info.status == OrderStatus.CANCELED:
            _notify_protective_recovery(
                trade_execution_service,
                active_trade,
                f"Take-profit {take_profit_info.id} по {active_trade.symbol} отменен — восстанавливаем защиту.",
                exchange_name,
            )
            _restore_limit_orders(
                trade_execution_service=trade_execution_service,
                active_trade=active_trade,
                context=context,
                protective_order_statuses=protective_order_statuses,
                order_type="take-profit",
            )
        if take_profit_info.average_price:
            active_trade.exit_average_price = take_profit_info.average_price

    stop_loss_info = _fetch_order_status(exchange, active_trade.symbol, protective_orders.stop_loss_id)
    if stop_loss_info:
        _log_status_change(
            "stop-loss",
            protective_orders.stop_loss_status and protective_orders.stop_loss_status.value,
            stop_loss_info.status.value,
            stop_loss_info.id,
        )
        protective_orders.stop_loss_status = stop_loss_info.status
        protective_order_statuses.stop_loss = stop_loss_info
        protective_order_statuses.stop_loss_status = stop_loss_info.status
        if stop_loss_info.status == OrderStatus.CANCELED:
            _notify_protective_recovery(
                trade_execution_service,
                active_trade,
                f"Stop-loss {stop_loss_info.id} по {active_trade.symbol} отменен — переставляем защиту.",
                exchange_name,
            )
            _restore_stop_orders(
                trade_execution_service=trade_execution_service,
                active_trade=active_trade,
                context=context,
                protective_order_statuses=protective_order_statuses,
                to_breakeven=False,
            )
        if stop_loss_info.average_price:
            active_trade.exit_average_price = stop_loss_info.average_price

    breakeven_info = _fetch_order_status(exchange, active_trade.symbol, protective_orders.breakeven_id)
    if breakeven_info:
        _log_status_change(
            "breakeven",
            protective_orders.breakeven_status and protective_orders.breakeven_status.value,
            breakeven_info.status.value,
            breakeven_info.id,
        )
        protective_orders.breakeven_status = breakeven_info.status
        protective_order_statuses.breakeven = breakeven_info
        protective_order_statuses.breakeven_status = breakeven_info.status
        if breakeven_info.status == OrderStatus.CANCELED:
            _notify_protective_recovery(
                trade_execution_service,
                active_trade,
                f"Breakeven {breakeven_info.id} по {active_trade.symbol} отменен — восстанавливаем защиту.",
                exchange_name,
            )
            _restore_stop_orders(
                trade_execution_service=trade_execution_service,
                active_trade=active_trade,
                context=context,
                protective_order_statuses=protective_order_statuses,
                to_breakeven=True,
            )
        if breakeven_info.average_price:
            active_trade.exit_average_price = breakeven_info.average_price

    partial_close_info = _fetch_order_status(exchange, active_trade.symbol, protective_orders.partial_close_id)
    if partial_close_info:
        _log_status_change(
            "partial-close",
            protective_orders.partial_close_status and protective_orders.partial_close_status.value,
            partial_close_info.status.value,
            partial_close_info.id,
        )
        protective_orders.partial_close_status = partial_close_info.status
        protective_order_statuses.partial_close = partial_close_info
        protective_order_statuses.partial_close_status = partial_close_info.status
        if partial_close_info.status == OrderStatus.CANCELED:
            _notify_protective_recovery(
                trade_execution_service,
                active_trade,
                f"Partial-close {partial_close_info.id} по {active_trade.symbol} отменен — пробуем восстановить.",
                exchange_name,
            )
            _restore_limit_orders(
                trade_execution_service=trade_execution_service,
                active_trade=active_trade,
                context=context,
                protective_order_statuses=protective_order_statuses,
                order_type="partial-close",
            )
        if partial_close_info.average_price:
            active_trade.exit_average_price = partial_close_info.average_price

    return protective_order_statuses


# noinspection DuplicatedCode
def _process_filled_partial_close(
        trade_execution_service: TradeExecutionService,
        exchange: Exchange,
        active_trade: ActiveTrade,
        context: _TradeMonitorContext,
        protective_order_statuses: ProtectiveOrderStatuses,
):
    partial_close_info = protective_order_statuses.partial_close
    breakeven_info = protective_order_statuses.breakeven
    stop_loss_info = protective_order_statuses.stop_loss
    if not partial_close_info or partial_close_info.status != OrderStatus.FILLED:
        return breakeven_info

    filled_quantity = partial_close_info.filled or partial_close_info.quantity
    if filled_quantity is None:
        return breakeven_info

    remaining_quantity = (active_trade.quantity or 0) - filled_quantity
    context.remaining_quantity = max(remaining_quantity, 0)

    try:
        previous_stop_loss_id = context.protective_orders.stop_loss_id
        previous_breakeven_id = context.protective_orders.breakeven_id
        realigned_ids = trade_execution_service.realign_stop_orders(
            setup=active_trade.setup,
            stop_loss_order_id=context.protective_orders.stop_loss_id,
            breakeven_order_id=context.protective_orders.breakeven_id,
            stop_loss_status=context.protective_orders.stop_loss_status,
            breakeven_status=context.protective_orders.breakeven_status,
            remaining_quantity=context.remaining_quantity,
            move_to_breakeven=active_trade.setup.breakeven_price is not None,
        )
        log.i(
            "Результат перестановки стоп-ордеров: "
            f"SL {previous_stop_loss_id} → {realigned_ids.stop_loss_id}, "
            f"BE {previous_breakeven_id} → {realigned_ids.breakeven_id}"
        )

        realigned_stop_loss_id = realigned_ids.stop_loss_id
        realigned_breakeven_id = realigned_ids.breakeven_id

        if realigned_stop_loss_id != previous_stop_loss_id:
            context.protective_orders.stop_loss_id = realigned_stop_loss_id
            context.protective_orders.stop_loss_status = (
                    realigned_ids.stop_loss_status
                    or (OrderStatus.NEW if realigned_stop_loss_id else None)
            )
            protective_order_statuses.stop_loss = None
            protective_order_statuses.stop_loss_status = context.protective_orders.stop_loss_status
        elif previous_stop_loss_id:
            refreshed_stop_info = stop_loss_info or _fetch_order_status(
                exchange, active_trade.symbol, previous_stop_loss_id
            )
            if refreshed_stop_info:
                context.protective_orders.stop_loss_status = refreshed_stop_info.status
                protective_order_statuses.stop_loss = refreshed_stop_info
                protective_order_statuses.stop_loss_status = refreshed_stop_info.status

        if realigned_breakeven_id != previous_breakeven_id:
            context.protective_orders.breakeven_id = realigned_breakeven_id
            context.protective_orders.breakeven_status = (
                    realigned_ids.breakeven_status
                    or (OrderStatus.NEW if realigned_breakeven_id else None)
            )
            protective_order_statuses.breakeven = None
            protective_order_statuses.breakeven_status = context.protective_orders.breakeven_status
        elif previous_breakeven_id:
            refreshed_breakeven_info = breakeven_info or _fetch_order_status(
                exchange, active_trade.symbol, previous_breakeven_id
            )
            if refreshed_breakeven_info:
                context.protective_orders.breakeven_status = refreshed_breakeven_info.status
                protective_order_statuses.breakeven = refreshed_breakeven_info
                protective_order_statuses.breakeven_status = refreshed_breakeven_info.status
    except Exception as exception:
        log.e(f"Не удалось обновить защитные ордера после частичного закрытия {active_trade.symbol}: {exception}")

    return protective_order_statuses.breakeven


def _calculate_partial_exit_metrics(
        context: _TradeMonitorContext,
        active_trade: ActiveTrade,
        partial_close_info,
        exit_prices: list[float],
) -> tuple[float, float, float, float, float]:
    entry_price = context.entry_price
    exit_price = partial_close_info.average_price or (active_trade.setup.partial_close_price or entry_price)
    _add_exit_price(exit_prices, exit_price)

    total_quantity = active_trade.quantity or 0
    partial_quantity = partial_close_info.filled or partial_close_info.quantity or total_quantity * 0.5
    remaining_quantity = (
        context.remaining_quantity
        if context.remaining_quantity is not None
        else max(total_quantity - partial_quantity, 0)
    )
    total_quantity = total_quantity or (partial_quantity + remaining_quantity)
    context.remaining_quantity = remaining_quantity

    return entry_price, exit_price, total_quantity, partial_quantity, remaining_quantity


def _classify_partial_close_breakeven(
        active_trade: ActiveTrade,
        breakeven_info,
        entry_price: float,
        exit_price: float,
        total_quantity: float,
        partial_quantity: float,
        remaining_quantity: float,
        exit_prices: list[float],
) -> Optional[tuple[TradeResult, float, float, list[float]]]:
    if not breakeven_info or breakeven_info.status != OrderStatus.FILLED:
        return None

    breakeven_exit_price = breakeven_info.average_price or active_trade.setup.breakeven_price or exit_price
    _add_exit_price(exit_prices, breakeven_exit_price)
    profit_value, profit_pct = _calculate_split_profit(
        entry_price,
        total_quantity,
        exit_price,
        partial_quantity,
        breakeven_exit_price,
        remaining_quantity,
    )
    active_trade.status = OrderStatus.PARTIALLY_FILLED
    return TradeResult.PC_BE, profit_pct, profit_value, exit_prices


# noinspection DuplicatedCode
def _classify_partial_close_take_profit(
        active_trade: ActiveTrade,
        take_profit_info,
        entry_price: float,
        exit_price: float,
        total_quantity: float,
        partial_quantity: float,
        remaining_quantity: float,
        exit_prices: list[float],
) -> Optional[tuple[TradeResult, float, float, list[float]]]:
    if not take_profit_info or take_profit_info.status != OrderStatus.FILLED:
        return None

    take_profit_exit_price = take_profit_info.average_price or active_trade.setup.take_profit_price
    _add_exit_price(exit_prices, take_profit_exit_price)
    profit_value, profit_pct = _calculate_split_profit(
        entry_price,
        total_quantity,
        exit_price,
        partial_quantity,
        take_profit_exit_price,
        remaining_quantity,
    )
    active_trade.status = OrderStatus.FILLED
    return TradeResult.PC_TP, profit_pct, profit_value, exit_prices


# noinspection DuplicatedCode
def _classify_partial_close_stop_loss(
        active_trade: ActiveTrade,
        stop_loss_info,
        entry_price: float,
        exit_price: float,
        total_quantity: float,
        partial_quantity: float,
        remaining_quantity: float,
        exit_prices: list[float],
) -> Optional[tuple[TradeResult, float, float, list[float]]]:
    if not stop_loss_info or stop_loss_info.status != OrderStatus.FILLED:
        return None

    stop_loss_exit_price = stop_loss_info.average_price or active_trade.setup.stop_loss_price
    _add_exit_price(exit_prices, stop_loss_exit_price)
    profit_value, profit_pct = _calculate_split_profit(
        entry_price,
        total_quantity,
        exit_price,
        partial_quantity,
        stop_loss_exit_price,
        remaining_quantity,
    )
    active_trade.status = OrderStatus.FILLED
    return TradeResult.SL, profit_pct, profit_value, exit_prices


def _process_partial_close_outcome(
        context: _TradeMonitorContext,
        active_trade: ActiveTrade,
        protective_order_statuses: ProtectiveOrderStatuses,
        remaining_quantity_for_remainder: Optional[float],
        exit_prices: list[float],
) -> tuple[Optional[tuple[TradeResult, float, float, list[float]]], Optional[float]]:
    partial_close_info = protective_order_statuses.partial_close
    if not partial_close_info or partial_close_info.status != OrderStatus.FILLED:
        return None, remaining_quantity_for_remainder

    entry_price, exit_price, total_quantity, partial_quantity, remaining_quantity_for_remainder = (
        _calculate_partial_exit_metrics(context, active_trade, partial_close_info, exit_prices)
    )

    breakeven_outcome = _classify_partial_close_breakeven(
        active_trade,
        protective_order_statuses.breakeven,
        entry_price,
        exit_price,
        total_quantity,
        partial_quantity,
        remaining_quantity_for_remainder,
        exit_prices,
    )
    if breakeven_outcome:
        return breakeven_outcome, remaining_quantity_for_remainder

    take_profit_outcome = _classify_partial_close_take_profit(
        active_trade,
        protective_order_statuses.take_profit,
        entry_price,
        exit_price,
        total_quantity,
        partial_quantity,
        remaining_quantity_for_remainder,
        exit_prices,
    )
    if take_profit_outcome:
        return take_profit_outcome, remaining_quantity_for_remainder

    stop_loss_outcome = _classify_partial_close_stop_loss(
        active_trade,
        protective_order_statuses.stop_loss,
        entry_price,
        exit_price,
        total_quantity,
        partial_quantity,
        remaining_quantity_for_remainder,
        exit_prices,
    )
    if stop_loss_outcome:
        return stop_loss_outcome, remaining_quantity_for_remainder

    return None, remaining_quantity_for_remainder


# noinspection DuplicatedCode
def _process_full_exit_outcome(
        context: _TradeMonitorContext,
        active_trade: ActiveTrade,
        protective_order_statuses: ProtectiveOrderStatuses,
        remaining_quantity_for_remainder: Optional[float],
        exit_prices: list[float],
) -> Optional[tuple[TradeResult, float, float, list[float]]]:
    take_profit_info = protective_order_statuses.take_profit
    if take_profit_info and take_profit_info.status == OrderStatus.FILLED:
        exit_price = take_profit_info.average_price or active_trade.setup.take_profit_price
        _add_exit_price(exit_prices, exit_price)
        profit_value, profit_pct = _calculate_profit(
            context.entry_price,
            exit_price,
            remaining_quantity_for_remainder if remaining_quantity_for_remainder is not None else active_trade.quantity,
        )
        active_trade.status = OrderStatus.FILLED
        return TradeResult.TP, profit_pct, profit_value, exit_prices

    stop_loss_info = protective_order_statuses.stop_loss
    if stop_loss_info and stop_loss_info.status == OrderStatus.FILLED:
        exit_price = stop_loss_info.average_price or active_trade.setup.stop_loss_price
        _add_exit_price(exit_prices, exit_price)
        profit_value, profit_pct = _calculate_profit(
            context.entry_price,
            exit_price,
            remaining_quantity_for_remainder if remaining_quantity_for_remainder is not None else active_trade.quantity,
        )
        active_trade.status = OrderStatus.FILLED
        return TradeResult.SL, profit_pct, profit_value, exit_prices

    return None


def _process_final_position_outcome(
        exchange: Exchange,
        active_trade: ActiveTrade,
        position: Optional[Position],
        context: _TradeMonitorContext,
        exit_prices: list[float],
) -> Optional[tuple[TradeResult, float, float, list[float]]]:
    if position is None:
        try:
            position = exchange.get_position(active_trade.symbol)
        except NotImplementedError:
            position = None
        except Exception as exception:
            log.e(f"Не удалось получить позицию {active_trade.symbol}: {exception}")
            position = None

    if not position and active_trade.entry_order_status == OrderStatus.FILLED:
        if active_trade.exit_average_price:
            exit_price = active_trade.exit_average_price
            _add_exit_price(exit_prices, exit_price)
            profit_value, profit_pct = _calculate_profit(context.entry_price, exit_price, active_trade.quantity)
        else:
            profit_value = 0.0
            profit_pct = 0.0
        active_trade.status = OrderStatus.FILLED
        return TradeResult.MANUAL, profit_pct, profit_value, exit_prices

    if position and position.pnl is not None:
        return None

    return None


def _calculate_trade_outcome(
        exchange: Exchange,
        active_trade: ActiveTrade,
        position: Optional[Position],
        context: _TradeMonitorContext,
        protective_order_statuses: ProtectiveOrderStatuses,
) -> Optional[tuple[TradeResult, float, float, list[float]]]:
    exit_prices: list[float] = []
    remaining_quantity_for_remainder = _get_remaining_quantity(context, active_trade)

    outcome, remaining_quantity_for_remainder = _process_partial_close_outcome(
        context=context,
        active_trade=active_trade,
        protective_order_statuses=protective_order_statuses,
        remaining_quantity_for_remainder=remaining_quantity_for_remainder,
        exit_prices=exit_prices,
    )
    if outcome:
        return outcome

    outcome = _process_full_exit_outcome(
        context=context,
        active_trade=active_trade,
        protective_order_statuses=protective_order_statuses,
        remaining_quantity_for_remainder=remaining_quantity_for_remainder,
        exit_prices=exit_prices,
    )
    if outcome:
        return outcome

    return _process_final_position_outcome(
        exchange=exchange,
        active_trade=active_trade,
        position=position,
        context=context,
        exit_prices=exit_prices,
    )


def _monitor_active_trade(
        exchange: Exchange,
        trade_execution_service: TradeExecutionService,
        active_trade: ActiveTrade,
        bars: list[Bar],
        exchange_name: str,
) -> Optional[tuple[TradeResult, float, float, list[float]]]:
    try:
        _update_postmortem_bars(active_trade, bars)

        position, early_outcome = _update_entry_status(exchange, trade_execution_service, active_trade)
        if early_outcome:
            return early_outcome

        context = _build_trade_monitor_context(active_trade)

        protective_order_statuses = _refresh_protective_orders(
            exchange,
            trade_execution_service,
            active_trade,
            context,
            exchange_name,
        )

        breakeven_info = _process_filled_partial_close(
            trade_execution_service=trade_execution_service,
            exchange=exchange,
            active_trade=active_trade,
            context=context,
            protective_order_statuses=protective_order_statuses,
        )

        protective_order_statuses.breakeven = breakeven_info
        protective_order_statuses.breakeven_status = (
            breakeven_info.status if breakeven_info else context.protective_orders.breakeven_status
        )

        active_trade.remaining_quantity = context.remaining_quantity
        active_trade.protective_orders = context.protective_orders

        return _calculate_trade_outcome(
            exchange=exchange,
            active_trade=active_trade,
            position=position,
            context=context,
            protective_order_statuses=protective_order_statuses,
        )
    except Exception as exception:
        log.e(f"Ошибка при мониторинге сделки {active_trade.symbol}: {exception}")
        return None


# endregion

def run_live(
        strategy: Strategy,
        exchange: Exchange,
        notifier: Notifier,
        timeframes: list[Timeframe],
        limit: int,
        listing_period_days: int,
        volume_24h_new_usdt_min: int,
        volume_24h_old_usdt_min: int,
        trades_24h_min: int,
        trades_24h_btc_ratio_min: float
) -> None:
    exchange_name = exchange.get_name()
    log.d(f"Запуск в живом режиме на бирже {exchange_name}.")

    if not timeframes:
        log.e("Не заданы таймфреймы.")
        return

    filtered_symbols = _fetch_filtered_symbols(
        exchange,
        listing_period_days,
        volume_24h_new_usdt_min,
        volume_24h_old_usdt_min,
        trades_24h_min,
        trades_24h_btc_ratio_min
    )
    if not filtered_symbols:
        log.e("Не хватает символов для начала анализа.")
        return

    capture_state = CaptureState()
    trade_permission_service = TradePermissionService()
    trade_execution_service = TradeExecutionService(exchange, notifier)
    notifier.start_callback_handler(trade_permission_service)
    executor = ThreadPoolExecutor(max_workers=2)
    handle_sig(executor)
    notified_once: set[tuple[str, Timeframe, str]] = set()
    active_trades = ActiveTrades()

    scheduled_tasks = [
        ScheduledTask(next_run_at=utc_now(), symbol=symbol, timeframe=timeframe)
        for symbol in filtered_symbols
        for timeframe in timeframes
    ]
    heapify(scheduled_tasks)

    while True:
        now = utc_now()
        expired_permissions = trade_permission_service.pop_expired(now)
        for permission_key, expired_permission in expired_permissions:
            _remove_button(notifier, expired_permission.message_id)
            log.i(f"Истекло разрешение на торговлю {permission_key.symbol} на {permission_key.timeframe.tf}.")
            notified_once.discard((permission_key.symbol, permission_key.timeframe, "Capture"))
        expired_ignored = trade_permission_service.pop_expired_ignored(now)
        for ignored_key, expired_ignore in expired_ignored:
            _remove_button(notifier, expired_ignore.message_id)
            log.i(f"Истек срок игнорирования {ignored_key.symbol} на {ignored_key.timeframe.tf}.")
        expired_captures = [
            (capture_key, active_capture)
            for capture_key, active_capture in capture_state.captures.items()
            if now >= active_capture.deadline
        ]
        for capture_key, active_capture in expired_captures:
            capture_state.remove_capture(capture_key)
            _remove_button(notifier, active_capture.message_id)
            trade_permission_service.clear_allowance(capture_key.symbol, capture_key.timeframe)
            log.i(f"Истек срок слежения за {capture_key.symbol} на {capture_key.timeframe.tf}.")
            notified_once.discard((capture_key.symbol, capture_key.timeframe, "Capture"))

        cycle_started = False
        while scheduled_tasks and scheduled_tasks[0].next_run_at <= now:
            task = heappop(scheduled_tasks)
            symbol = task.symbol
            timeframe = task.timeframe

            if capture_state.is_empty() and not cycle_started:
                log.d("Запуск цикла анализа отобранных монет.")
                cycle_started = True

            try:
                capture_key = CaptureKey(symbol.symbol, timeframe)
                active_capture = capture_state.get_capture(capture_key)
                if active_capture and now < active_capture.deadline:
                    log.d(f"Продолжаем мониторинг Capture {symbol.symbol} на {timeframe.tf}.")
                if trade_permission_service.has_ignore(symbol.symbol, timeframe):
                    log.d(f"Пропущен анализ {symbol.symbol} на {timeframe.tf} из-за активного игнорирования.")
                    continue
                setup = None
                bars = []
                for timeframe_limit in calculate_limit_grid(limit, timeframe):
                    bars = exchange.get_ohlcv(symbol.symbol, timeframe, timeframe_limit)
                    setup = strategy.detect_setup(symbol.symbol, bars, timeframe, symbol.context)
                    if setup:
                        break
                trade_key = ActiveTradeKey(symbol.symbol, timeframe)
                active_trade = active_trades.get(trade_key)
                if active_trade:
                    outcome = _monitor_active_trade(
                        exchange,
                        trade_execution_service,
                        active_trade,
                        bars,
                        exchange_name,
                    )
                    if outcome:
                        trade_result, profit_pct, profit_value, exit_prices = outcome
                        log.i(
                            f"Завершена сделка {symbol.symbol} {timeframe.tf}: "
                            f"{trade_result.value} ({profit_pct:+.2f}%)"
                        )
                        active_trade = active_trades.remove(trade_key)
                        active_trade.exit_average_price = active_trade.exit_average_price or (
                            exit_prices[0] if exit_prices else None)
                        _notify_trade_closure(
                            notifier=notifier,
                            active_trade=active_trade,
                            trade_result=trade_result,
                            profit_pct=profit_pct,
                            profit_value=profit_value,
                            exit_prices=exit_prices,
                            exchange_name=exchange_name,
                        )
                        trade_permission_service.clear_allowance(symbol.symbol, timeframe)
                        continue
                match setup:
                    # Сетап не найден.
                    case Unfilled():
                        removed_capture = capture_state.remove_capture(capture_key)
                        if removed_capture:
                            _remove_button(notifier, removed_capture.message_id)
                            log.i(f"Сетап {symbol.symbol} на {timeframe.tf} потерян, кнопка удалена.")
                            trade_permission_service.clear_allowance(symbol.symbol, timeframe)
                            notified_once.discard((symbol.symbol, timeframe, "Capture"))
                        continue

                    # Найден базовый сетап.
                    case Capture():
                        if trade_permission_service.has_allowance(symbol.symbol, timeframe):
                            log.d( f"Пропущено уведомление Capture для {symbol.symbol} на {timeframe.tf} "
                                   f"из-за активного разрешения на торговлю.")
                            continue
                        capture_key = CaptureKey(symbol.symbol, timeframe)
                        message = build_capture_message(symbol, timeframe, exchange_name)
                        if capture_key in capture_state.captures:
                            active_capture = capture_state.captures.get(capture_key)
                            if active_capture.message_id:
                                executor.submit(
                                    _edit_notification,
                                    notifier=notifier,
                                    notification_type=NotificationType.EVENT,
                                    message_id=active_capture.message_id,
                                    message=message,
                                    setup=setup,
                                    subdir='event',
                                    context=symbol.context,
                                    keyboard=_build_keyboard(symbol.symbol, timeframe, symbol.context),
                                ).result()
                            continue
                        capture_notification_key = (symbol.symbol, timeframe, setup.name)
                        if capture_notification_key not in notified_once:
                            message_id = executor.submit(
                                _send_notification,
                                notifier=notifier,
                                notification_type=NotificationType.EVENT,
                                message=message,
                                setup=setup,
                                subdir='event',
                                context=symbol.context,
                                keyboard=_build_keyboard(symbol.symbol, timeframe, symbol.context),
                            ).result()
                            capture_state.add_capture(
                                symbol=symbol.symbol,
                                timeframe=timeframe,
                                message_id=message_id,
                                timeout_multiplier=cfg.CAPTURE_TIMEOUT_MULTIPLIER,
                            )
                            notified_once.add(capture_notification_key)

                    # Найден торговый сетап.
                    case Trade():
                        if not trade_permission_service.has_allowance(symbol.symbol, timeframe):
                            log.i(f"Отказ в открытии сделки {symbol.symbol} на {timeframe.tf}: "
                                  f"отсутствует разрешение на торговлю.")
                            continue
                        log.i(f"Попытка открытия позиции в {symbol.symbol} на {timeframe.tf}.")
                        execution_result = trade_execution_service.execute_buy(setup, symbol.context)
                        if not execution_result:
                            trade_permission_service.clear_allowance(symbol.symbol, timeframe)
                            continue
                        success_message = build_trade_opened_message(
                            setup=setup,
                            timeframe=timeframe,
                            execution_result=execution_result,
                            exchange_name=exchange_name,
                        )
                        log.i(success_message)
                        executor.submit(
                            _send_notification,
                            notifier=notifier,
                            notification_type=NotificationType.ORDER,
                            message=success_message,
                            setup=setup,
                            subdir='order',
                            context=symbol.context
                        ).result()
                        detection_time = bars[-1].time if bars else utc_now()
                        removed_capture = capture_state.remove_capture(capture_key)
                        capture_message_id = removed_capture.message_id if removed_capture else None
                        if removed_capture:
                            _remove_button(notifier, removed_capture.message_id)
                            log.i(f"Сетап {symbol.symbol} на {timeframe.tf} закрыт из-за сигнала Trade, кнопка удалена.")
                            notified_once.discard((symbol.symbol, timeframe, "Capture"))
                        trade = ActiveTrade(
                            symbol=symbol.symbol,
                            timeframe=timeframe,
                            setup=setup,
                            detection_time=detection_time,
                            context=symbol.context,
                            placed_at=utc_now(),
                            quantity=execution_result.quantity,
                            entry_order_status=OrderStatus.NEW,
                            capture_message_id=capture_message_id,
                            entry_order_id=execution_result.entry_order_id,
                            protective_orders=execution_result.protective_orders,
                            position_id=execution_result.position_id,
                        )
                        active_trades.add(trade_key, trade)
                        trade_permission_service.clear_allowance(symbol.symbol, timeframe)
            finally:
                next_run_at = utc_now() + TIMEFRAME_INTERVALS[timeframe]
                heappush(
                    scheduled_tasks,
                    ScheduledTask(
                        next_run_at=next_run_at,
                        symbol=symbol,
                        timeframe=timeframe,
                    )
                )
                now = utc_now()

        if scheduled_tasks:
            sleep_seconds = max(0.0, (scheduled_tasks[0].next_run_at - now).total_seconds())
            if sleep_seconds > 0:
                sleep(min(sleep_seconds, 60))
