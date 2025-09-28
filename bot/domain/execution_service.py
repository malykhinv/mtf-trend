"""Execution service responsible for translating signals into trades."""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Literal, Optional, TypeVar

from bot import config
from bot.utils.logger import get_logger
from exchange_client import (
    BracketOrderRequest,
    ExchangeClient,
    ExchangeClientError,
    OrderExecutionSnapshot,
    OrderStatus,
    PositionStatus,
    SymbolPositionSnapshot,
)
from models.close_reason import CloseReason
from models.signal import Signal
from models.signal_direction import SignalDirection
from models.trade import Trade
from models.trade_status import TradeStatus


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
    POSITION_POLL_ATTEMPTS = 3
    POSITION_POLL_INTERVAL_SEC = 0.5

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
        direction_name = "лонг" if signal.direction.is_long else "шорт"
        self._logger.info(
            "Выставляем ордера для %s %s %s: количество %.4f",
            signal.exchange.value,
            signal.symbol,
            direction_name,
            quantity,
        )
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
        self._logger.info(
            "Заявка %s принята: статус %s, исполнено %.4f",
            trade_id,
            entry.status.value,
            entry.filled_qty,
        )

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

        self._logger.info(
            "Закрываем сделку %s по причине %s, цена %.4f",
            trade.trade_id,
            reason.value,
            price,
        )
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
        self._logger.info(
            "Результат закрытия %s: статус %s, объём %.4f, цена %.4f",
            trade.trade_id,
            final_status.value,
            final_qty,
            avg_fill_price if avg_fill_price is not None else 0.0,
        )

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

    def poll_trade_state(
        self,
        trade: Trade,
        *,
        awaiting_close: bool = False,
        poll_attempts: int | None = None,
        poll_interval_sec: float | None = None,
    ) -> "TradePollingResult | None":
        """Poll exchange state and return a result if trade status changed."""

        attempts = max(1, poll_attempts or self.POSITION_POLL_ATTEMPTS)
        interval = poll_interval_sec or self.POSITION_POLL_INTERVAL_SEC

        for _ in range(attempts):
            try:
                snapshot = self._with_retry(
                    lambda: self._exchange.fetch_position(exchange=trade.exchange, symbol=trade.symbol),
                    context=f"fetch position for {trade.exchange.value}:{trade.symbol}",
                )
            except ExchangeClientError:
                return None

            result = self._interpret_position_snapshot(trade, snapshot, awaiting_close=awaiting_close)
            if result is not None:
                return result
            time.sleep(interval)

        return None

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

    # noinspection PyUnreachableCode
    @staticmethod
    def _map_reason_to_status(reason: CloseReason) -> TradeStatus:
        if reason is CloseReason.TAKE_PROFIT:
            return TradeStatus.CLOSED_TP
        if reason is CloseReason.STOP_LOSS:
            return TradeStatus.CLOSED_SL
        if reason in (CloseReason.AGGRESSION, CloseReason.MANUAL):
            return TradeStatus.CLOSED_MANUAL
        return TradeStatus.CANCELLED

    def _interpret_position_snapshot(
        self,
        trade: Trade,
        snapshot: SymbolPositionSnapshot,
        *,
        awaiting_close: bool,
    ) -> "TradePollingResult | None":
        if snapshot.status is PositionStatus.NONE:
            if trade.status is TradeStatus.CANCELLED:
                return None
            cancelled_trade = replace(
                trade,
                status=TradeStatus.CANCELLED,
                executed_qty=0.0,
                reason_close=trade.reason_close or CloseReason.MANUAL,
                timestamp_close=snapshot.updated_at or datetime.now(tz=config.TIMEZONE),
            )
            return TradePollingResult(trade=cancelled_trade, outcome="cancelled")

        if snapshot.status is PositionStatus.OPEN:
            entry = snapshot.entry
            if entry is None:
                return None
            updates: dict[str, object] = {}
            new_status = self._map_order_status_to_trade_status(entry.status)
            if entry.filled_qty != trade.executed_qty:
                updates["executed_qty"] = entry.filled_qty
            if entry.avg_fill_price is not None and entry.avg_fill_price != trade.avg_fill_price:
                updates["avg_fill_price"] = entry.avg_fill_price
            if entry.updated_at and entry.updated_at != trade.timestamp_open:
                updates["timestamp_open"] = entry.updated_at
            if new_status != trade.status:
                updates["status"] = new_status

            if not updates:
                return None

            updated_trade = replace(trade, **updates)
            if new_status is TradeStatus.OPENED:
                return TradePollingResult(trade=updated_trade, outcome="filled")
            if new_status is TradeStatus.CANCELLED:
                return TradePollingResult(trade=updated_trade, outcome="cancelled")
            return None

        close_snapshot, reason = self._resolve_close_snapshot(trade, snapshot)
        final_status = self._map_reason_to_status(reason)
        updates = {
            "reason_close": reason,
        }

        if final_status != trade.status:
            updates["status"] = final_status

        if close_snapshot and close_snapshot.filled_qty != trade.executed_qty:
            updates["executed_qty"] = close_snapshot.filled_qty

        if close_snapshot and close_snapshot.avg_fill_price is not None and close_snapshot.avg_fill_price != trade.avg_fill_price:
            updates["avg_fill_price"] = close_snapshot.avg_fill_price

        if close_snapshot and close_snapshot.order_id != trade.close_order_id:
            updates["close_order_id"] = close_snapshot.order_id

        timestamp_close = (
            (close_snapshot.updated_at if close_snapshot and close_snapshot.updated_at else snapshot.updated_at)
            or trade.timestamp_close
            or datetime.now(tz=config.TIMEZONE)
        )

        if trade.timestamp_close != timestamp_close:
            updates["timestamp_close"] = timestamp_close

        updated_trade = replace(trade, **updates) if updates else trade

        if awaiting_close or updates:
            return TradePollingResult(trade=updated_trade, outcome="closed")
        return None

    @staticmethod
    def _resolve_close_snapshot(
            trade: Trade,
        snapshot: SymbolPositionSnapshot,
    ) -> tuple[OrderExecutionSnapshot | None, CloseReason]:
        take_snapshot = snapshot.take
        stop_snapshot = snapshot.stop
        close_snapshot = snapshot.close

        if take_snapshot and take_snapshot.status is OrderStatus.FILLED:
            return take_snapshot, CloseReason.TAKE_PROFIT

        if stop_snapshot and stop_snapshot.status is OrderStatus.FILLED:
            return stop_snapshot, CloseReason.STOP_LOSS

        if close_snapshot and close_snapshot.status is OrderStatus.FILLED:
            return close_snapshot, trade.reason_close or CloseReason.MANUAL

        if close_snapshot:
            return close_snapshot, trade.reason_close or CloseReason.MANUAL

        if take_snapshot and take_snapshot.status is OrderStatus.CANCELLED and trade.reason_close is CloseReason.TAKE_PROFIT:
            return take_snapshot, CloseReason.TAKE_PROFIT

        if stop_snapshot and stop_snapshot.status is OrderStatus.CANCELLED and trade.reason_close is CloseReason.STOP_LOSS:
            return stop_snapshot, CloseReason.STOP_LOSS

        return snapshot.entry, trade.reason_close or CloseReason.MANUAL


@dataclass(frozen=True)
class TradePollingResult:
    trade: Trade
    outcome: Literal["filled", "closed", "cancelled"]
