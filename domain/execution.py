from __future__ import annotations

import logging
from typing import Callable, Optional, Tuple

from config.config import CONFIG
from config.models import MarginMode as ConfigMarginMode
from config.models import StopTrigger as ConfigStopTrigger
from domain.models import (
    ExecutionReport,
    MarginMode,
    Side,
    Signal,
    StopTrigger,
    SymbolFilters,
    Wall,
)
from domain.strategy.types import PositionEntryHandler, PositionExitHandler, StopMoveHandler
from domain.trading_adapter import TradingAdapter
from utils.mathx import ceil_to_step, compute_position_size, floor_to_step

SymbolFiltersProvider = Callable[[str], SymbolFilters]
BalanceProvider = Callable[[str], float]


def initialize_account(trading: TradingAdapter) -> None:
    margin_mode: MarginMode = _map_margin_mode(CONFIG.position.margin_mode)
    logger = logging.getLogger(__name__)
    try:
        actual_leverage = trading.set_leverage(CONFIG.position.leverage, margin_mode)
    except Exception as exc:
        logger.warning(
            "Failed to set leverage %s in %s margin mode: %s",
            CONFIG.position.leverage,
            margin_mode.name,
            exc,
        )
        return
    if actual_leverage is not None and actual_leverage != CONFIG.position.leverage:
        logger.warning(
            "Leverage adjusted from %s to %s for margin mode %s.",
            CONFIG.position.leverage,
            actual_leverage,
            margin_mode.name,
        )


def create_execution_handlers(
    trading: TradingAdapter,
    filters_provider: SymbolFiltersProvider,
    balance_provider: BalanceProvider,
) -> Tuple[PositionEntryHandler, PositionExitHandler, StopMoveHandler]:
    stop_trigger: StopTrigger = _map_stop_trigger(CONFIG.position.stop_trigger)
    current_symbol: Optional[str] = None
    current_signal: Signal = Signal.NONE
    current_quantity: float = 0.0
    logger = logging.getLogger(__name__)

    def place_market(
        side: Side,
        quantity: float,
        *,
        symbol: str,
        signal: Signal,
        reason: Optional[str] = None,
    ) -> Optional[ExecutionReport]:
        try:
            report = trading.place_market(side, quantity, reason=reason)
        except Exception as exc:
            if reason:
                logger.error(
                    "Market order failed: %s %s qty=%.8f reason=%s signal=%s: %s",
                    symbol,
                    side.name,
                    quantity,
                    reason,
                    signal.name,
                    exc,
                )
            else:
                logger.error(
                    "Market order failed: %s %s qty=%.8f signal=%s: %s",
                    symbol,
                    side.name,
                    quantity,
                    signal.name,
                    exc,
                )
            return None
        logger.info(
            "Market order executed: %s %s qty=%.8f executed=%.8f price=%.8f status=%s",
            symbol,
            side.name,
            quantity,
            report.executed_qty,
            report.price,
            report.status,
        )
        return report

    def place_stop_market(
        side: Side,
        stop_price: float,
        quantity: float,
        *,
        symbol: str,
        signal: Signal,
    ) -> bool:
        try:
            trading.place_stop_market(side, stop_price, quantity, stop_trigger)
        except Exception as exc:
            logger.error(
                "Stop order failed: %s %s qty=%.8f stop=%.8f signal=%s: %s",
                symbol,
                side.name,
                quantity,
                stop_price,
                signal.name,
                exc,
            )
            return False
        logger.info(
            "Stop order placed: %s %s qty=%.8f stop=%.8f signal=%s",
            symbol,
            side.name,
            quantity,
            stop_price,
            signal.name,
        )
        return True

    def enter(symbol: str, signal: Signal, wall: Wall) -> bool:
        nonlocal current_symbol, current_signal, current_quantity
        filters: SymbolFilters = filters_provider(symbol)
        quantity_step: float = filters.quantity_step_size
        price: float = _estimate_entry_price(signal, wall.price, filters)
        balance: float = balance_provider(symbol)
        quantity: float = compute_position_size(
            balance,
            CONFIG.position.position_fraction,
            float(CONFIG.position.position_min_usdt),
            price,
            quantity_step,
        )
        if quantity <= 0.0:
            return False
        min_reference: float = max(filters.min_qty, quantity_step)
        min_quantity: float = ceil_to_step(min_reference, quantity_step)
        if min_quantity > 0.0 and quantity < min_quantity:
            quantity = min_quantity
        if filters.max_qty > 0.0:
            max_quantity: float = floor_to_step(filters.max_qty, quantity_step)
            if max_quantity <= 0.0:
                return False
            if quantity > max_quantity:
                quantity = max_quantity
        quantity = floor_to_step(quantity, quantity_step)
        if quantity < filters.min_qty or quantity <= 0.0:
            return False
        side: Side = _signal_to_entry_side(signal)
        report = place_market(
            side,
            quantity,
            symbol=symbol,
            signal=signal,
        )
        if report is None:
            return False
        report_quantity: float = report.executed_qty
        if report_quantity <= 0.0:
            report_quantity = quantity
        executed_quantity: float = floor_to_step(report_quantity, quantity_step)
        if executed_quantity <= 0.0:
            logger.error(
                "Executed quantity invalid: %s %s qty=%.8f signal=%s",
                symbol,
                side.name,
                report_quantity,
                signal.name,
            )
            return False
        current_symbol = symbol
        current_signal = signal
        current_quantity = executed_quantity
        stop_price: float = _compute_stop_price(signal, wall.price, filters)
        stop_side: Side = _signal_to_exit_side(signal)
        place_stop_market(
            stop_side,
            stop_price,
            current_quantity,
            symbol=symbol,
            signal=signal,
        )
        return True

    def exit(symbol: str, reason: str) -> bool:
        nonlocal current_symbol, current_signal, current_quantity
        if symbol != current_symbol:
            return False
        if current_signal is Signal.NONE or current_quantity <= 0.0:
            current_symbol = None
            current_signal = Signal.NONE
            current_quantity = 0.0
            return True
        side: Side = _signal_to_exit_side(current_signal)
        report = place_market(
            side,
            current_quantity,
            symbol=symbol,
            signal=current_signal,
            reason=reason,
        )
        if report is None:
            return False
        current_symbol = None
        current_signal = Signal.NONE
        current_quantity = 0.0
        return True

    def move_stop(symbol: str, wall_price: float) -> bool:
        if symbol != current_symbol:
            return False
        if current_signal is Signal.NONE or current_quantity <= 0.0:
            return False
        filters: SymbolFilters = filters_provider(symbol)
        stop_price = _compute_stop_price(current_signal, wall_price, filters)
        stop_side: Side = _signal_to_exit_side(current_signal)
        return place_stop_market(
            stop_side,
            stop_price,
            current_quantity,
            symbol=symbol,
            signal=current_signal,
        )

    return enter, exit, move_stop


def _estimate_entry_price(signal: Signal, wall_price: float, filters: SymbolFilters) -> float:
    tick_size: float = filters.price_tick_size
    if tick_size <= 0.0:
        raise ValueError("tick size must be positive")
    if signal is Signal.LONG:
        candidate: float = wall_price + tick_size
    elif signal is Signal.SHORT:
        candidate = wall_price - tick_size
        if candidate <= 0.0:
            candidate = wall_price
    else:
        raise ValueError("unsupported signal")
    return _clamp_price(candidate, filters)


def _compute_stop_price(signal: Signal, wall_price: float, filters: SymbolFilters) -> float:
    tick_size: float = filters.price_tick_size
    if tick_size <= 0.0:
        raise ValueError("tick size must be positive")
    if signal is Signal.LONG:
        candidate: float = wall_price - tick_size
        stop_price: float = floor_to_step(candidate, tick_size)
        bounded: float = max(stop_price, filters.min_price)
        return bounded
    if signal is Signal.SHORT:
        candidate = wall_price + tick_size
        stop_price = ceil_to_step(candidate, tick_size)
        bounded = stop_price
        if filters.max_price > 0.0:
            bounded = min(bounded, filters.max_price)
        return bounded
    raise ValueError("unsupported signal")


def _clamp_price(price: float, filters: SymbolFilters) -> float:
    bounded: float = price
    if filters.min_price > 0.0 and bounded < filters.min_price:
        bounded = filters.min_price
    if 0.0 < filters.max_price < bounded:
        bounded = filters.max_price
    return bounded


def _signal_to_entry_side(signal: Signal) -> Side:
    if signal is Signal.LONG:
        return Side.BID
    if signal is Signal.SHORT:
        return Side.ASK
    raise ValueError("unsupported signal")


def _signal_to_exit_side(signal: Signal) -> Side:
    if signal is Signal.LONG:
        return Side.ASK
    if signal is Signal.SHORT:
        return Side.BID
    raise ValueError("unsupported signal")


def _map_margin_mode(source: ConfigMarginMode) -> MarginMode:
    if source is ConfigMarginMode.ISOLATED:
        return MarginMode.ISOLATED
    if source is ConfigMarginMode.CROSS:
        return MarginMode.CROSS
    raise ValueError("unsupported margin mode")


def _map_stop_trigger(source: ConfigStopTrigger) -> StopTrigger:
    if source is ConfigStopTrigger.MARK_PRICE:
        return StopTrigger.MARK
    if source is ConfigStopTrigger.LAST_PRICE:
        return StopTrigger.LAST
    raise ValueError("unsupported stop trigger")


__all__ = ["SymbolFiltersProvider", "initialize_account", "create_execution_handlers"]
