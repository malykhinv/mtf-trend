from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, Sequence

import ccxt

from execution import ExecutionEventHandler, OrderUpdateEvent
from infrastructure import get_logger
from state import SymbolState


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True)
class _OrderSnapshot:
    symbol: str
    order_id: str
    status: str
    filled: float
    remaining: float
    average_price: float | None
    timestamp: datetime | None


@dataclass(frozen=True)
class _TradeSnapshot:
    order_id: str
    trade_id: str
    amount: float
    timestamp: datetime | None


@dataclass
class OrderWatcher:
    """Poll REST endpoints to monitor the lifecycle of active orders."""

    exchange: ccxt.Exchange
    event_handler: ExecutionEventHandler
    states: Dict[str, SymbolState]
    order_poll_interval: float
    trade_poll_interval: float
    now_factory: Callable[[], datetime] = field(default_factory=lambda: _utc_now)
    logger: logging.Logger = field(default_factory=lambda: get_logger(__name__))

    def __post_init__(self) -> None:
        self._order_cache: Dict[str, _OrderSnapshot] = {}
        self._trade_fills: Dict[str, float] = {}
        self._trade_timestamps: Dict[str, datetime] = {}
        self._order_trade_ids: Dict[str, set[str]] = {}
        self._last_events: Dict[str, tuple[float, str]] = {}
        self._next_orders_poll: datetime | None = None
        self._next_trades_poll: datetime | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def poll(self) -> None:
        """Poll the exchange for updates respecting configured intervals."""

        active_symbols = self._collect_active_symbols()
        tracked_ids = self._collect_tracked_order_ids(active_symbols)
        self._prune_caches(tracked_ids)
        if not active_symbols:
            self._next_orders_poll = None
            self._next_trades_poll = None
            return

        now = self.now_factory()
        if any(order_id not in self._last_events for order_id in tracked_ids):
            self._next_orders_poll = now
            self._next_trades_poll = now

        should_fetch_orders = self._next_orders_poll is None or now >= self._next_orders_poll
        should_fetch_trades = self._next_trades_poll is None or now >= self._next_trades_poll

        order_snapshots: list[_OrderSnapshot] = []
        if should_fetch_orders:
            order_snapshots = self._fetch_orders(active_symbols, tracked_ids)
            self._next_orders_poll = now + timedelta(seconds=self.order_poll_interval)

        trade_snapshots: list[_TradeSnapshot] = []
        if should_fetch_trades:
            trade_snapshots = self._fetch_trades(active_symbols, tracked_ids)
            self._next_trades_poll = now + timedelta(seconds=self.trade_poll_interval)

        if not order_snapshots and not trade_snapshots:
            return

        if order_snapshots:
            self._update_order_cache(order_snapshots)
        if trade_snapshots:
            self._update_trade_cache(trade_snapshots)

        self._emit_events(now)

    def time_until_next_poll(self, now: datetime | None = None) -> float | None:
        """Return seconds until the next scheduled poll or ``None`` if idle."""

        now_value = now or self.now_factory()
        candidates: list[float] = []
        if self._next_orders_poll is not None:
            candidates.append((self._next_orders_poll - now_value).total_seconds())
        if self._next_trades_poll is not None:
            candidates.append((self._next_trades_poll - now_value).total_seconds())
        if not candidates:
            return None
        positive = [value for value in candidates if value > 0.0]
        if positive:
            return min(positive)
        return 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _collect_active_symbols(self) -> list[str]:
        symbols: list[str] = []
        for symbol, state in self.states.items():
            if state.active_orders:
                symbols.append(symbol)
        return symbols

    def _collect_tracked_order_ids(self, active_symbols: Iterable[str]) -> set[str]:
        tracked: set[str] = set()
        for symbol in active_symbols:
            state = self.states.get(symbol)
            if state is None:
                continue
            for active in state.active_orders.values():
                tracked.add(active.order_id)
        return tracked

    def _prune_caches(self, tracked_ids: set[str]) -> None:
        for order_id in list(self._order_cache):
            if order_id not in tracked_ids:
                self._order_cache.pop(order_id, None)
        for order_id in list(self._trade_fills):
            if order_id not in tracked_ids:
                self._trade_fills.pop(order_id, None)
        for order_id in list(self._trade_timestamps):
            if order_id not in tracked_ids:
                self._trade_timestamps.pop(order_id, None)
        for order_id in list(self._order_trade_ids):
            if order_id not in tracked_ids:
                self._order_trade_ids.pop(order_id, None)
        for order_id in list(self._last_events):
            if order_id not in tracked_ids:
                self._last_events.pop(order_id, None)

    def _fetch_orders(
        self,
        symbols: Sequence[str],
        tracked_ids: set[str],
    ) -> list[_OrderSnapshot]:
        snapshots: list[_OrderSnapshot] = []
        for symbol in symbols:
            try:
                raw_orders = self.exchange.fetch_orders(symbol)
            except ccxt.BaseError as exc:  # pragma: no cover - network interaction
                self.logger.warning("Не удалось получить ордера %s: %s", symbol, exc)
                continue
            for order in raw_orders:
                order_id_raw = order.get("id") or order.get("orderId")
                if order_id_raw is None:
                    continue
                order_id = str(order_id_raw)
                if tracked_ids and order_id not in tracked_ids:
                    continue
                filled = self._to_float(order.get("filled"))
                amount = self._to_float(order.get("amount"))
                remaining_raw = order.get("remaining")
                if remaining_raw is None and amount > 0:
                    remaining = max(amount - filled, 0.0)
                else:
                    remaining = max(self._to_float(remaining_raw), 0.0)
                status_raw = order.get("status")
                status = str(status_raw).lower() if status_raw is not None else "open"
                average_raw = order.get("average") or order.get("avgPrice")
                average_price = self._to_float_or_none(average_raw)
                timestamp = self._parse_timestamp(order.get("timestamp"), order.get("datetime"))
                snapshots.append(
                    _OrderSnapshot(
                        symbol=symbol,
                        order_id=order_id,
                        status=status,
                        filled=filled,
                        remaining=remaining,
                        average_price=average_price,
                        timestamp=timestamp,
                    )
                )
        return snapshots

    def _fetch_trades(
        self,
        symbols: Sequence[str],
        tracked_ids: set[str],
    ) -> list[_TradeSnapshot]:
        trades: list[_TradeSnapshot] = []
        for symbol in symbols:
            try:
                raw_trades = self.exchange.fetch_my_trades(symbol)
            except ccxt.BaseError as exc:  # pragma: no cover - network interaction
                self.logger.warning("Не удалось получить сделки %s: %s", symbol, exc)
                continue
            for trade in raw_trades:
                order_id_raw = trade.get("order") or trade.get("orderId")
                if order_id_raw is None:
                    continue
                order_id = str(order_id_raw)
                if tracked_ids and order_id not in tracked_ids:
                    continue
                trade_id_raw = trade.get("id") or trade.get("tradeId")
                timestamp = self._parse_timestamp(trade.get("timestamp"), trade.get("datetime"))
                if trade_id_raw is None:
                    trade_id = f"{order_id}:{trade.get('amount')}:{trade.get('price')}:{int(timestamp.timestamp() * 1000) if timestamp else 'na'}"
                else:
                    trade_id = str(trade_id_raw)
                amount = self._to_float(trade.get("amount"))
                if amount <= 0:
                    continue
                trades.append(
                    _TradeSnapshot(
                        order_id=order_id,
                        trade_id=trade_id,
                        amount=amount,
                        timestamp=timestamp,
                    )
                )
        return trades

    def _update_order_cache(self, snapshots: Iterable[_OrderSnapshot]) -> None:
        for snapshot in snapshots:
            self._order_cache[snapshot.order_id] = snapshot

    def _update_trade_cache(self, trades: Iterable[_TradeSnapshot]) -> None:
        for trade in trades:
            order_trades = self._order_trade_ids.setdefault(trade.order_id, set())
            if trade.trade_id in order_trades:
                continue
            order_trades.add(trade.trade_id)
            current = self._trade_fills.get(trade.order_id, 0.0)
            self._trade_fills[trade.order_id] = current + trade.amount
            if trade.timestamp is not None:
                existing = self._trade_timestamps.get(trade.order_id)
                if existing is None or trade.timestamp > existing:
                    self._trade_timestamps[trade.order_id] = trade.timestamp

    def _emit_events(self, default_timestamp: datetime) -> None:
        for symbol, state in list(self.states.items()):
            if not state.active_orders:
                continue
            updated_state = state
            for _, active_order in list(state.active_orders.items()):
                order_id = active_order.order_id
                snapshot = self._order_cache.get(order_id)
                filled_candidates = [active_order.filled]
                if snapshot is not None:
                    filled_candidates.append(snapshot.filled)
                trade_fill = self._trade_fills.get(order_id)
                if trade_fill is not None:
                    filled_candidates.append(trade_fill)
                filled = max(filled_candidates)
                remaining = snapshot.remaining if snapshot is not None else max(active_order.quantity - filled, 0.0)
                average_price = snapshot.average_price if snapshot is not None else None
                timestamp = (
                    snapshot.timestamp
                    if snapshot is not None and snapshot.timestamp is not None
                    else self._trade_timestamps.get(order_id)
                )
                if timestamp is None:
                    timestamp = default_timestamp
                previous = self._last_events.get(order_id)
                status = snapshot.status if snapshot is not None else (previous[1] if previous is not None else "open")
                if previous is not None and self._is_close(previous[0], filled) and previous[1] == status:
                    continue
                event = OrderUpdateEvent(
                    symbol=symbol,
                    order_id=order_id,
                    status=status,
                    filled=filled,
                    remaining=max(remaining, 0.0),
                    average_price=average_price,
                    timestamp=timestamp,
                )
                updated_state = self.event_handler.handle_order_update(updated_state, event)
                self.states[symbol] = updated_state
                self._last_events[order_id] = (filled, status)
                if order_id not in updated_state.active_orders:
                    self._cleanup_order(order_id)
            self.states[symbol] = updated_state

    def _cleanup_order(self, order_id: str) -> None:
        self._order_cache.pop(order_id, None)
        self._trade_fills.pop(order_id, None)
        self._trade_timestamps.pop(order_id, None)
        self._order_trade_ids.pop(order_id, None)
        self._last_events.pop(order_id, None)

    def _parse_timestamp(self, timestamp_raw: object, datetime_raw: object) -> datetime | None:
        if isinstance(timestamp_raw, (int, float)):
            return datetime.fromtimestamp(float(timestamp_raw) / 1000.0, tz=timezone.utc)
        if isinstance(datetime_raw, str):
            parsed = self.exchange.parse8601(datetime_raw)
            if parsed is not None:
                return datetime.fromtimestamp(parsed / 1000.0, tz=timezone.utc)
        return None

    @staticmethod
    def _to_float(value: object) -> float:
        try:
            if value is None:
                return 0.0
            if isinstance(value, str) and not value.strip():
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _to_float_or_none(value: object) -> float | None:
        try:
            if value is None:
                return None
            if isinstance(value, str) and not value.strip():
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_close(left: float, right: float, *, tol: float = 1e-8) -> bool:
        return abs(left - right) <= tol


__all__ = ["OrderWatcher"]
