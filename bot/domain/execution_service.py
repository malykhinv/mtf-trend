"""Execution service responsible for translating signals into trades."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, TypeVar

from bot import config
from bot.utils.logging import get_logger
from .exchange_client import (
    BracketOrderExecution,
    BracketOrderRequest,
    ExchangeClient,
    ExchangeClientError,
    OrderExecutionSnapshot,
    OrderStatus,
)
from .models.close_reason import CloseReason
from .models.signal import Signal
from .models.signal_direction import SignalDirection
from .models.trade import Trade
from .models.trade_status import TradeStatus


@dataclass(frozen=True)
class ExecutionSettings:
    min_order_usdt: float = config.MIN_ORDER_USDT
    order_pct_of_deposit: float = config.ORDER_PCT_OF_DEPOSIT
    breakeven_trigger_pct: float = config.BREAKEVEN_TRIGGER_PCT
    breakeven_offset_pct: float = config.BREAKEVEN_OFFSET_PCT


_T = TypeVar("_T")


class ExecutionService:
    """Translate domain signals into concrete exchange operations."""

    RETRY_ATTEMPTS = 3
    RETRY_DELAY_SEC = 0.5

    def __init__(
        self,
        exchange_client: ExchangeClient,
        *,
        settings: ExecutionSettings | None = None,
        retry_attempts: int | None = None,
        retry_delay_sec: float | None = None,
    ) -> None:
        self._exchange = exchange_client
        self._settings = settings or ExecutionSettings()
        self._latest_imbalance: dict[str, float] = {}
        self._retry_attempts = max(1, retry_attempts or self.RETRY_ATTEMPTS)
        self._retry_delay_sec = retry_delay_sec or self.RETRY_DELAY_SEC
        self._logger = get_logger(__name__)

    def calc_order_size_usdt(self, deposit_usdt: float) -> float:
        base_size = deposit_usdt * self._settings.order_pct_of_deposit
        return max(self._settings.min_order_usdt, base_size)

    def open_trade(
        self,
        signal: Signal,
        quantity: float,
        timestamp: Optional[datetime] = None,
    ) -> Trade:
        levels = signal.levels
        trade_id = f"trade-{signal.signal_id}"
        request = BracketOrderRequest(
            client_trade_id=trade_id,
            exchange=signal.exchange,
            symbol=signal.symbol,
            side=signal.direction,
            quantity=quantity,
            entry_price=levels.entry_price,
            take_profit_price=levels.take_profit_price,
            stop_loss_price=levels.stop_loss_price,
        )

        execution = self._with_retry(
            lambda: self._exchange.submit_bracket_order(request),
            context=f"submit bracket order for {trade_id}",
        )

        entry = execution.entry
        trade_timestamp = entry.updated_at or timestamp or signal.timestamp
        trade_status = self._map_order_status_to_trade_status(entry.status)

        return Trade(
            trade_id=trade_id,
            source_signal_id=signal.signal_id,
            exchange=signal.exchange,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            side=signal.direction,
            timestamp_open=trade_timestamp,
            entry_price=levels.entry_price,
            take_profit_price=levels.take_profit_price,
            stop_loss_price=levels.stop_loss_price,
            requested_qty=quantity,
            executed_qty=entry.filled_qty,
            status=trade_status,
            avg_fill_price=entry.avg_fill_price,
            order_id=entry.order_id,
            stop_order_id=execution.stop_order_id,
            take_order_id=execution.take_order_id,
        )

    def close_trade(
        self,
        trade: Trade,
        reason: CloseReason,
        price: float,
        timestamp: datetime,
    ) -> Trade:
        entry_snapshot = self._fetch_optional(trade.order_id, context="fetch entry order before close")
        executed_qty = trade.executed_qty
        avg_entry_price = trade.avg_fill_price

        if entry_snapshot is not None:
            executed_qty = entry_snapshot.filled_qty
            if entry_snapshot.avg_fill_price is not None:
                avg_entry_price = entry_snapshot.avg_fill_price

        stop_snapshot = self._fetch_optional(trade.stop_order_id, context="fetch stop order before close")
        take_snapshot = self._fetch_optional(trade.take_order_id, context="fetch take order before close")

        close_snapshot: OrderExecutionSnapshot | None = None
        close_order_id = trade.close_order_id

        if reason is CloseReason.TAKE_PROFIT and take_snapshot and take_snapshot.status is OrderStatus.FILLED:
            close_snapshot = take_snapshot
            close_order_id = take_snapshot.order_id
        elif reason is CloseReason.STOP_LOSS and stop_snapshot and stop_snapshot.status is OrderStatus.FILLED:
            close_snapshot = stop_snapshot
            close_order_id = stop_snapshot.order_id
        else:
            if (
                trade.order_id
                and entry_snapshot is not None
                and entry_snapshot.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED)
            ):
                try:
                    entry_snapshot = self._with_retry(
                        lambda: self._exchange.cancel_order(trade.order_id),
                        context=f"cancel entry order {trade.order_id}",
                    )
                except ExchangeClientError:
                    self._logger.warning("Не удалось отменить ордер %s", trade.order_id)

            if entry_snapshot is not None:
                executed_qty = entry_snapshot.filled_qty
                if entry_snapshot.avg_fill_price is not None:
                    avg_entry_price = entry_snapshot.avg_fill_price

            if executed_qty > 0:
                try:
                    close_snapshot = self._with_retry(
                        lambda: self._exchange.close_position_market(
                            exchange=trade.exchange,
                            symbol=trade.symbol,
                            side=trade.side,
                            quantity=executed_qty,
                            price_hint=price,
                        ),
                        context=f"close position for {trade.trade_id}",
                    )
                    close_order_id = close_snapshot.order_id
                except ExchangeClientError:
                    self._logger.exception(
                        "Не удалось закрыть сделку %s по рынку", trade.trade_id
                    )

        self._cancel_if_open(trade.stop_order_id, stop_snapshot)
        self._cancel_if_open(trade.take_order_id, take_snapshot)

        timestamp_close = timestamp
        if close_snapshot and close_snapshot.updated_at is not None:
            timestamp_close = close_snapshot.updated_at

        final_qty = close_snapshot.filled_qty if close_snapshot else executed_qty
        avg_fill_price = (
            close_snapshot.avg_fill_price
            if close_snapshot and close_snapshot.avg_fill_price is not None
            else price if final_qty > 0 else avg_entry_price
        )

        final_status = self._derive_close_status(reason, close_snapshot, final_qty)

        return Trade(
            trade_id=trade.trade_id,
            source_signal_id=trade.source_signal_id,
            exchange=trade.exchange,
            symbol=trade.symbol,
            timeframe=trade.timeframe,
            side=trade.side,
            timestamp_open=trade.timestamp_open,
            entry_price=trade.entry_price,
            take_profit_price=trade.take_profit_price,
            stop_loss_price=trade.stop_loss_price,
            requested_qty=trade.requested_qty,
            executed_qty=final_qty,
            status=final_status,
            timestamp_close=timestamp_close,
            avg_fill_price=avg_fill_price,
            reason_close=reason,
            sl_be_at=trade.sl_be_at,
            order_id=trade.order_id,
            stop_order_id=trade.stop_order_id,
            take_order_id=trade.take_order_id,
            close_order_id=close_order_id,
        )

    def should_move_to_breakeven(self, entry_price: float, last_price: float, side: SignalDirection) -> bool:
        """Return True when price progress warrants moving the stop to break-even."""

        move_pct = self._settings.breakeven_trigger_pct / 100.0
        if side.is_long:
            return last_price >= entry_price * (1 + move_pct)
        return last_price <= entry_price * (1 - move_pct)

    def breakeven_stop(self, entry_price: float, side: SignalDirection) -> float:
        offset_pct = self._settings.breakeven_offset_pct / 100.0
        if side.is_long:
            return entry_price * (1 + offset_pct)
        return entry_price * (1 - offset_pct)

    def record_imbalance(self, symbol_key: str, imbalance: float) -> None:
        """Store the latest computed imbalance for a symbol."""

        self._latest_imbalance[symbol_key] = imbalance

    def last_recorded_imbalance(self, symbol_key: str) -> float | None:
        """Return last recorded imbalance if available."""

        return self._latest_imbalance.get(symbol_key)

    def _with_retry(self, func: Callable[[], _T], *, context: str) -> _T:
        attempt = 1
        delay = self._retry_delay_sec
        while True:
            try:
                return func()
            except ExchangeClientError as exc:
                if attempt >= self._retry_attempts:
                    self._logger.error(
                        "Операция биржи '%s' завершилась ошибкой после %s попыток", context, attempt
                    )
                    raise
                self._logger.warning(
                    "Операция биржи '%s' не удалась на попытке %s/%s: %s",
                    context,
                    attempt,
                    self._retry_attempts,
                    exc,
                )
                time.sleep(delay)
                delay *= 2
                attempt += 1

    def _fetch_optional(
        self, order_id: str | None, *, context: str
    ) -> OrderExecutionSnapshot | None:
        if order_id is None:
            return None
        try:
            return self._with_retry(lambda: self._exchange.fetch_order(order_id), context=context)
        except ExchangeClientError:
            self._logger.warning("Не удалось получить состояние ордера %s", order_id)
            return None

    def _cancel_if_open(
        self, order_id: str | None, snapshot: OrderExecutionSnapshot | None
    ) -> None:
        if order_id is None:
            return
        state = snapshot or self._fetch_optional(order_id, context=f"refresh order {order_id}")
        if state is None:
            return
        if state.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED):
            try:
                self._with_retry(lambda: self._exchange.cancel_order(order_id), context=f"cancel order {order_id}")
            except ExchangeClientError:
                self._logger.warning("Не удалось отменить ордер %s", order_id)

    @staticmethod
    def _map_order_status_to_trade_status(status: OrderStatus) -> TradeStatus:
        if status is OrderStatus.NEW:
            return TradeStatus.PENDING
        if status is OrderStatus.PARTIALLY_FILLED:
            return TradeStatus.PARTIALLY_FILLED
        if status is OrderStatus.FILLED:
            return TradeStatus.OPENED
        return TradeStatus.CANCELLED

    def _derive_close_status(
        self,
        reason: CloseReason,
        snapshot: OrderExecutionSnapshot | None,
        executed_qty: float,
    ) -> TradeStatus:
        if executed_qty <= 0:
            return TradeStatus.CANCELLED
        if snapshot is None:
            return self._map_reason_to_status(reason)
        mapped = self._map_order_status_to_trade_status(snapshot.status)
        if mapped is TradeStatus.OPENED:
            return self._map_reason_to_status(reason)
        return mapped

    @staticmethod
    def _map_reason_to_status(reason: CloseReason) -> TradeStatus:
        if reason is CloseReason.TAKE_PROFIT:
            return TradeStatus.CLOSED_TP
        if reason is CloseReason.STOP_LOSS:
            return TradeStatus.CLOSED_SL
        if reason in (CloseReason.AGGRESSION, CloseReason.MANUAL):
            return TradeStatus.CLOSED_MANUAL
        return TradeStatus.CANCELLED
