"""Client abstractions for interacting with external exchanges."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol
from urllib.parse import urlencode
from uuid import uuid4

import requests

from bot import config
from bot.domain.models.exchange import Exchange
from bot.domain.models.signal_direction import SignalDirection


def _normalize_symbol_for_api(symbol: str) -> str:
    """Return symbol formatted without separators for REST API requests."""

    normalized = symbol.strip().upper()
    for separator in ("/", "-"):
        normalized = normalized.replace(separator, "")
    return normalized


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

    def fetch_positions(self, *, exchange: Exchange) -> list[SymbolPositionSnapshot]:
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

    def fetch_positions(self, *, exchange: Exchange) -> list[SymbolPositionSnapshot]:
        snapshots: list[SymbolPositionSnapshot] = []
        for (exchange_value, symbol), _ in self._positions.items():
            if exchange_value != exchange.value:
                continue
            snapshots.append(self.fetch_position(exchange=exchange, symbol=symbol))
        return snapshots

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


class BinanceExchangeClient(ExchangeClient):
    """Concrete exchange client that talks to Binance Futures REST API."""

    _ORDER_ENDPOINT = "/fapi/v1/order"
    _POSITION_ENDPOINT = "/fapi/v2/positionRisk"

    _BINANCE_STATUS_MAP: dict[str, OrderStatus] = {
        "NEW": OrderStatus.NEW,
        "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
        "FILLED": OrderStatus.FILLED,
        "CANCELED": OrderStatus.CANCELLED,
        "PENDING_CANCEL": OrderStatus.CANCELLED,
        "EXPIRED": OrderStatus.CANCELLED,
        "REJECTED": OrderStatus.CANCELLED,
    }

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str | None = None,
        recv_window: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self._base_url = (base_url or config.BINANCE_API_URL).rstrip("/")
        self._recv_window = recv_window if recv_window is not None else config.BINANCE_RECV_WINDOW
        self._timeout = timeout or 10.0
        self._session = requests.Session()
        self._logger = logging.getLogger(self.__class__.__name__)
        self._order_cache: dict[str, OrderExecutionSnapshot] = {}
        self._order_symbol: dict[str, str] = {}
        self._symbol_orders: dict[tuple[str, str], dict[str, object]] = {}

    def submit_bracket_order(self, request: BracketOrderRequest) -> BracketOrderExecution:
        symbol_display = request.symbol.upper()
        symbol_api = _normalize_symbol_for_api(request.symbol)
        side = self._direction_to_side(request.side)
        entry_client_id = self._client_order_id(request.client_trade_id, "entry")
        stop_client_id = self._client_order_id(request.client_trade_id, "stop")
        take_client_id = self._client_order_id(request.client_trade_id, "take")

        entry_payload = {
            "symbol": symbol_api,
            "side": side,
            "type": "MARKET",
            "quantity": self._format_decimal(request.quantity),
            "newClientOrderId": entry_client_id,
        }

        entry_data_raw = self._signed_request("POST", self._ORDER_ENDPOINT, entry_payload)
        entry_data = self._expect_dict(entry_data_raw)
        entry_snapshot = self._snapshot_from_payload(entry_data)
        self._cache_order(entry_snapshot, symbol_display)

        opposing_side = self._opposite_side(request.side)
        stop_payload = {
            "symbol": symbol_api,
            "side": opposing_side,
            "type": "STOP_MARKET",
            "stopPrice": self._format_decimal(request.stop_loss_price),
            "quantity": self._format_decimal(request.quantity),
            "reduceOnly": "true",
            "newClientOrderId": stop_client_id,
        }

        take_payload = {
            "symbol": symbol_api,
            "side": opposing_side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": self._format_decimal(request.take_profit_price),
            "quantity": self._format_decimal(request.quantity),
            "reduceOnly": "true",
            "newClientOrderId": take_client_id,
        }

        try:
            stop_data_raw = self._signed_request("POST", self._ORDER_ENDPOINT, stop_payload)
            take_data_raw = self._signed_request("POST", self._ORDER_ENDPOINT, take_payload)
            stop_data = self._expect_dict(stop_data_raw)
            take_data = self._expect_dict(take_data_raw)
        except ExchangeClientError:
            try:
                self.cancel_order(entry_snapshot.order_id)
            except ExchangeClientError:
                self._logger.exception(
                    "Не удалось отменить входной ордер %s после ошибки", entry_snapshot.order_id
                )
            raise

        stop_snapshot = self._snapshot_from_payload(stop_data)
        take_snapshot = self._snapshot_from_payload(take_data)

        self._cache_order(stop_snapshot, symbol_display)
        self._cache_order(take_snapshot, symbol_display)

        key = (request.exchange.value, symbol_display)
        self._symbol_orders[key] = {
            "entry": entry_snapshot.order_id,
            "stop": stop_snapshot.order_id,
            "take": take_snapshot.order_id,
            "side": request.side,
            "quantity": float(request.quantity),
        }

        return BracketOrderExecution(
            entry=entry_snapshot,
            stop_order_id=stop_snapshot.order_id,
            take_order_id=take_snapshot.order_id,
        )

    def fetch_order(self, order_id: str) -> OrderExecutionSnapshot:
        return self._fetch_order_remote(order_id)

    def cancel_order(self, order_id: str) -> OrderExecutionSnapshot:
        symbol_display = self._order_symbol.get(order_id)
        if symbol_display is None:
            raise ExchangeClientError(f"Неизвестен символ ордера {order_id}")
        payload = {"symbol": _normalize_symbol_for_api(symbol_display), "orderId": order_id}
        data_raw = self._signed_request("DELETE", self._ORDER_ENDPOINT, payload)
        data = self._expect_dict(data_raw)
        snapshot = self._snapshot_from_payload(data)
        self._cache_order(snapshot, symbol_display)
        self._update_symbol_order(order_id, snapshot.status)
        return snapshot

    def close_position_market(
        self,
        *,
        exchange: Exchange,
        symbol: str,
        side: SignalDirection,
        quantity: float,
        price_hint: float | None = None,
    ) -> OrderExecutionSnapshot:
        if exchange is not Exchange.BINANCE:
            raise ExchangeClientError("Поддерживается только Binance Futures")

        symbol_fmt = symbol.upper()
        symbol_api = _normalize_symbol_for_api(symbol)
        opposing_side = self._opposite_side(side)
        close_client_id = self._client_order_id(f"close-{symbol_fmt}", uuid4().hex[:6])
        payload = {
            "symbol": symbol_api,
            "side": opposing_side,
            "type": "MARKET",
            "quantity": self._format_decimal(quantity),
            "reduceOnly": "true",
            "newClientOrderId": close_client_id,
        }
        if price_hint is not None:
            payload["priceProtect"] = "true"

        data_raw = self._signed_request("POST", self._ORDER_ENDPOINT, payload)
        data = self._expect_dict(data_raw)
        snapshot = self._snapshot_from_payload(data)
        self._cache_order(snapshot, symbol_fmt)

        key = (exchange.value, symbol_fmt)
        orders = self._symbol_orders.setdefault(key, {})
        orders["close"] = snapshot.order_id
        if "side" not in orders:
            orders["side"] = side
        if "quantity" not in orders:
            orders["quantity"] = float(quantity)

        return snapshot

    def fetch_position(
        self,
        *,
        exchange: Exchange,
        symbol: str,
    ) -> SymbolPositionSnapshot:
        if exchange is not Exchange.BINANCE:
            raise ExchangeClientError("Поддерживается только Binance Futures")

        symbol_fmt = symbol.upper()
        symbol_api = _normalize_symbol_for_api(symbol)
        params = {"symbol": symbol_api}
        data = self._signed_request("GET", self._POSITION_ENDPOINT, params)
        position_info = data[0] if isinstance(data, list) and data else None
        return self._build_position_snapshot(exchange, symbol_fmt, position_info)

    def fetch_positions(self, *, exchange: Exchange) -> list[SymbolPositionSnapshot]:
        if exchange is not Exchange.BINANCE:
            raise ExchangeClientError("Поддерживается только Binance Futures")

        data = self._signed_request("GET", self._POSITION_ENDPOINT, {})
        snapshots: list[SymbolPositionSnapshot] = []
        if isinstance(data, list):
            for item in data:
                symbol_raw = str(item.get("symbol", ""))
                if not symbol_raw:
                    continue
                display_symbol = self._resolve_display_symbol(exchange, symbol_raw)
                snapshots.append(self._build_position_snapshot(exchange, display_symbol, item))
        return snapshots

    def _build_position_snapshot(
        self,
        exchange: Exchange,
        symbol: str,
        position_info: dict[str, object] | None,
    ) -> SymbolPositionSnapshot:
        quantity_value = 0.0
        side: SignalDirection | None = None
        updated_at: datetime | None = None

        if position_info:
            raw_quantity = self._safe_float(position_info.get("positionAmt"))
            update_time = self._safe_int(position_info.get("updateTime"))
            if update_time:
                updated_at = self._to_datetime(update_time)
            if raw_quantity is not None:
                quantity_value = abs(raw_quantity)
                if raw_quantity > 0:
                    side = SignalDirection.LONG
                elif raw_quantity < 0:
                    side = SignalDirection.SHORT

        key = (exchange.value, symbol)
        orders = self._symbol_orders.get(key, {})

        entry_snapshot = self._refresh_order_if_known(orders.get("entry"))
        stop_snapshot = self._refresh_order_if_known(orders.get("stop"))
        take_snapshot = self._refresh_order_if_known(orders.get("take"))
        close_snapshot = self._refresh_order_if_known(orders.get("close"))

        stored_side = orders.get("side")
        if side is None and stored_side is not None:
            side = stored_side  # type: ignore[assignment]

        status = PositionStatus.NONE
        if quantity_value > 0:
            status = PositionStatus.OPEN
        else:
            if close_snapshot and close_snapshot.status is OrderStatus.FILLED:
                status = PositionStatus.CLOSED
            elif stop_snapshot and stop_snapshot.status is OrderStatus.FILLED:
                status = PositionStatus.CLOSED
            elif take_snapshot and take_snapshot.status is OrderStatus.FILLED:
                status = PositionStatus.CLOSED
            elif entry_snapshot and entry_snapshot.status is OrderStatus.CANCELLED:
                status = PositionStatus.NONE
            elif entry_snapshot and entry_snapshot.status is OrderStatus.FILLED:
                status = PositionStatus.CLOSED

        if updated_at is None:
            timestamps = [
                snapshot.updated_at
                for snapshot in (entry_snapshot, stop_snapshot, take_snapshot, close_snapshot)
                if snapshot and snapshot.updated_at is not None
            ]
            updated_at = max(timestamps) if timestamps else None

        return SymbolPositionSnapshot(
            exchange=exchange,
            symbol=symbol,
            status=status,
            side=side,
            quantity=quantity_value,
            entry=entry_snapshot,
            stop=stop_snapshot,
            take=take_snapshot,
            close=close_snapshot,
            updated_at=updated_at,
        )

    def _resolve_display_symbol(self, exchange: Exchange, symbol: str) -> str:
        if not symbol:
            return symbol

        normalized = _normalize_symbol_for_api(symbol)
        for stored_symbol in self._order_symbol.values():
            if _normalize_symbol_for_api(stored_symbol) == normalized:
                return stored_symbol

        for exchange_value, stored_symbol in self._symbol_orders.keys():
            if exchange_value == exchange.value and _normalize_symbol_for_api(stored_symbol) == normalized:
                return stored_symbol

        return symbol.strip().upper()

    def _fetch_order_remote(self, order_id: str) -> OrderExecutionSnapshot:
        symbol_display = self._order_symbol.get(order_id)
        if symbol_display is None:
            raise ExchangeClientError(f"Неизвестен символ ордера {order_id}")
        payload = {"symbol": _normalize_symbol_for_api(symbol_display), "orderId": order_id}
        data_raw = self._signed_request("GET", self._ORDER_ENDPOINT, payload)
        data = self._expect_dict(data_raw)
        snapshot = self._snapshot_from_payload(data)
        self._cache_order(snapshot, symbol_display)
        return snapshot

    def _refresh_order_if_known(self, order_id: str | None) -> OrderExecutionSnapshot | None:
        if order_id is None:
            return None
        try:
            return self._fetch_order_remote(order_id)
        except ExchangeClientError:
            return self._order_cache.get(order_id)

    def _signed_request(
        self,
        method: str,
        path: str,
        params: dict[str, object],
    ) -> dict[str, object] | list[dict[str, object]]:
        timestamp = int(time.time() * 1000)
        payload = {key: value for key, value in params.items() if value is not None}
        payload["timestamp"] = timestamp
        if self._recv_window:
            payload["recvWindow"] = self._recv_window
        query = urlencode(payload, doseq=True)
        signature = hmac.new(self._api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
        payload["signature"] = signature

        url = f"{self._base_url}{path}"
        headers = {"X-MBX-APIKEY": self._api_key}

        try:
            response = self._session.request(
                method,
                url,
                params=payload,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:  # pragma: no cover - network errors
            raise ExchangeClientError(f"Ошибка сети при обращении к Binance API: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ExchangeClientError("Неверный формат ответа Binance API") from exc

        if isinstance(data, dict) and "code" in data and data.get("code") not in (None, 0):
            message = data.get("msg") or str(data)
            raise ExchangeClientError(f"Binance API вернула ошибку: {message}")

        return data

    def _snapshot_from_payload(self, payload: dict[str, object]) -> OrderExecutionSnapshot:
        order_id = str(payload.get("orderId"))
        status_raw = str(payload.get("status", "NEW"))
        status = self._BINANCE_STATUS_MAP.get(status_raw.upper(), OrderStatus.CANCELLED)
        filled_qty = self._safe_float(payload.get("executedQty")) or 0.0
        avg_fill = self._safe_float(payload.get("avgPrice"))
        if avg_fill is not None and avg_fill <= 0:
            avg_fill = None
        update_time = self._safe_int(
            payload.get("updateTime")
            or payload.get("workingTime")
            or payload.get("transactTime")
        )
        updated_at = self._to_datetime(update_time) if update_time else None

        return OrderExecutionSnapshot(
            order_id=order_id,
            status=status,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill,
            updated_at=updated_at,
        )

    def _cache_order(self, snapshot: OrderExecutionSnapshot, symbol: str) -> None:
        self._order_cache[snapshot.order_id] = snapshot
        self._order_symbol[snapshot.order_id] = symbol

    def _update_symbol_order(self, order_id: str, status: OrderStatus) -> None:
        if status is not OrderStatus.CANCELLED:
            return
        for orders in self._symbol_orders.values():
            for key, value in list(orders.items()):
                if isinstance(value, str) and value == order_id:
                    orders.pop(key, None)

    @staticmethod
    def _expect_dict(payload: dict[str, object] | list[dict[str, object]]) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ExchangeClientError("Неожиданный формат ответа Binance API")
        return payload

    @staticmethod
    def _direction_to_side(direction: SignalDirection) -> str:
        return "BUY" if direction.is_long else "SELL"

    @staticmethod
    def _opposite_side(direction: SignalDirection) -> str:
        return "SELL" if direction.is_long else "BUY"

    @staticmethod
    def _client_order_id(prefix: str, suffix: str) -> str:
        base = prefix.replace(" ", "")
        candidate = f"{base[:20]}-{suffix}"
        return candidate[:32]

    @staticmethod
    def _format_decimal(value: float) -> str:
        return ("{:.10f}".format(value)).rstrip("0").rstrip(".") or "0"

    @staticmethod
    def _safe_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_datetime(timestamp_ms: int) -> datetime:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=config.UTC).astimezone(config.TIMEZONE)


class BybitExchangeClient(ExchangeClient):
    """Exchange client implementation for Bybit USDT perpetual futures."""

    _ORDER_CREATE = "/v5/order/create"
    _ORDER_CANCEL = "/v5/order/cancel"
    _ORDER_QUERY = "/v5/order/realtime"
    _POSITION_LIST = "/v5/position/list"
    _CATEGORY = "linear"

    _BYBIT_STATUS_MAP: dict[str, OrderStatus] = {
        "CREATED": OrderStatus.NEW,
        "NEW": OrderStatus.NEW,
        "PENDINGCANCEL": OrderStatus.CANCELLED,
        "PARTIALLYFILLED": OrderStatus.PARTIALLY_FILLED,
        "FILLED": OrderStatus.FILLED,
        "CANCELLED": OrderStatus.CANCELLED,
        "PARTIALLYFILLED_CANCELLED": OrderStatus.CANCELLED,
        "REJECTED": OrderStatus.CANCELLED,
        "DEACTIVATED": OrderStatus.CANCELLED,
        "TRIGGERED": OrderStatus.NEW,
        "UNTRIGGERED": OrderStatus.NEW,
    }

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str | None = None,
        recv_window: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self._base_url = (base_url or config.BYBIT_API_URL).rstrip("/")
        self._recv_window = recv_window if recv_window is not None else config.BYBIT_RECV_WINDOW
        self._timeout = timeout or config.BYBIT_TIMEOUT
        self._session = requests.Session()
        self._logger = logging.getLogger(self.__class__.__name__)
        self._order_cache: dict[str, OrderExecutionSnapshot] = {}
        self._order_symbol: dict[str, str] = {}
        self._symbol_orders: dict[tuple[str, str], dict[str, object]] = {}

    def submit_bracket_order(self, request: BracketOrderRequest) -> BracketOrderExecution:
        if request.exchange is not Exchange.BYBIT:
            raise ExchangeClientError("Поддерживается только Bybit Perpetual")

        symbol_display = request.symbol.upper()
        symbol_api = _normalize_symbol_for_api(request.symbol)
        side = self._direction_to_side(request.side)
        entry_client_id = self._client_order_id(request.client_trade_id, "entry")
        stop_client_id = self._client_order_id(request.client_trade_id, "stop")
        take_client_id = self._client_order_id(request.client_trade_id, "take")

        entry_payload = {
            "category": self._CATEGORY,
            "symbol": symbol_api,
            "side": side,
            "orderType": "Market",
            "qty": self._format_decimal(request.quantity),
            "orderLinkId": entry_client_id,
        }

        entry_result = self._signed_request("POST", self._ORDER_CREATE, entry_payload)
        entry_id = self._extract_order_id(entry_result)
        self._order_symbol[entry_id] = symbol_display
        entry_snapshot = self._fetch_order_remote(entry_id, symbol_hint=symbol_display)

        opposing_side = self._opposite_side(request.side)
        stop_payload = {
            "category": self._CATEGORY,
            "symbol": symbol_api,
            "side": opposing_side,
            "orderType": "Market",
            "qty": self._format_decimal(request.quantity),
            "reduceOnly": True,
            "triggerBy": "LastPrice",
            "triggerPrice": self._format_decimal(request.stop_loss_price),
            "triggerDirection": self._trigger_direction_stop(request.side),
            "orderLinkId": stop_client_id,
            "tpslOrderType": "Market",
            "positionIdx": 0,
        }

        take_payload = {
            "category": self._CATEGORY,
            "symbol": symbol_api,
            "side": opposing_side,
            "orderType": "Market",
            "qty": self._format_decimal(request.quantity),
            "reduceOnly": True,
            "triggerBy": "LastPrice",
            "triggerPrice": self._format_decimal(request.take_profit_price),
            "triggerDirection": self._trigger_direction_take(request.side),
            "orderLinkId": take_client_id,
            "tpslOrderType": "Market",
            "positionIdx": 0,
        }

        try:
            stop_result = self._signed_request("POST", self._ORDER_CREATE, stop_payload)
            take_result = self._signed_request("POST", self._ORDER_CREATE, take_payload)
        except ExchangeClientError:
            try:
                self.cancel_order(entry_snapshot.order_id)
            except ExchangeClientError:
                self._logger.exception(
                    "Не удалось отменить входной ордер %s после ошибки", entry_snapshot.order_id
                )
            raise

        stop_id = self._extract_order_id(stop_result)
        take_id = self._extract_order_id(take_result)
        self._order_symbol[stop_id] = symbol_display
        self._order_symbol[take_id] = symbol_display

        stop_snapshot = self._fetch_order_remote(stop_id, symbol_hint=symbol_display)
        take_snapshot = self._fetch_order_remote(take_id, symbol_hint=symbol_display)

        self._cache_order(entry_snapshot, symbol_display)
        self._cache_order(stop_snapshot, symbol_display)
        self._cache_order(take_snapshot, symbol_display)

        key = (request.exchange.value, symbol_display)
        self._symbol_orders[key] = {
            "entry": entry_snapshot.order_id,
            "stop": stop_snapshot.order_id,
            "take": take_snapshot.order_id,
            "side": request.side,
            "quantity": float(request.quantity),
        }

        return BracketOrderExecution(
            entry=entry_snapshot,
            stop_order_id=stop_snapshot.order_id,
            take_order_id=take_snapshot.order_id,
        )

    def fetch_order(self, order_id: str) -> OrderExecutionSnapshot:
        return self._fetch_order_remote(order_id)

    def cancel_order(self, order_id: str) -> OrderExecutionSnapshot:
        symbol_display = self._order_symbol.get(order_id)
        if symbol_display is None:
            raise ExchangeClientError(f"Неизвестен символ ордера {order_id}")
        payload = {
            "category": self._CATEGORY,
            "symbol": _normalize_symbol_for_api(symbol_display),
            "orderId": order_id,
        }
        self._signed_request("POST", self._ORDER_CANCEL, payload)
        snapshot = self._fetch_order_remote(order_id, symbol_hint=symbol_display)
        self._update_symbol_order(order_id, snapshot.status)
        return snapshot

    def close_position_market(
        self,
        *,
        exchange: Exchange,
        symbol: str,
        side: SignalDirection,
        quantity: float,
        price_hint: float | None = None,
    ) -> OrderExecutionSnapshot:
        if exchange is not Exchange.BYBIT:
            raise ExchangeClientError("Поддерживается только Bybit Perpetual")

        symbol_fmt = symbol.upper()
        symbol_api = _normalize_symbol_for_api(symbol)
        opposing_side = self._opposite_side(side)
        close_client_id = self._client_order_id(f"close-{symbol_fmt}", uuid4().hex[:6])
        payload = {
            "category": self._CATEGORY,
            "symbol": symbol_api,
            "side": opposing_side,
            "orderType": "Market",
            "qty": self._format_decimal(quantity),
            "reduceOnly": True,
            "orderLinkId": close_client_id,
            "positionIdx": 0,
        }
        result = self._signed_request("POST", self._ORDER_CREATE, payload)
        order_id = self._extract_order_id(result)
        self._order_symbol[order_id] = symbol_fmt
        snapshot = self._fetch_order_remote(order_id, symbol_hint=symbol_fmt)
        self._cache_order(snapshot, symbol_fmt)

        key = (exchange.value, symbol_fmt)
        orders = self._symbol_orders.setdefault(key, {})
        orders["close"] = snapshot.order_id
        if "side" not in orders:
            orders["side"] = side
        if "quantity" not in orders:
            orders["quantity"] = float(quantity)

        return snapshot

    def fetch_position(
        self,
        *,
        exchange: Exchange,
        symbol: str,
    ) -> SymbolPositionSnapshot:
        if exchange is not Exchange.BYBIT:
            raise ExchangeClientError("Поддерживается только Bybit Perpetual")

        symbol_fmt = symbol.upper()
        symbol_api = _normalize_symbol_for_api(symbol)
        params = {"category": self._CATEGORY, "symbol": symbol_api}
        result = self._signed_request("GET", self._POSITION_LIST, params)
        position_info = None
        if isinstance(result, dict):
            items = result.get("list")
            if isinstance(items, list) and items:
                position_info = items[0]
        return self._build_position_snapshot(exchange, symbol_fmt, position_info)

    def fetch_positions(self, *, exchange: Exchange) -> list[SymbolPositionSnapshot]:
        if exchange is not Exchange.BYBIT:
            raise ExchangeClientError("Поддерживается только Bybit Perpetual")

        params = {"category": self._CATEGORY}
        result = self._signed_request("GET", self._POSITION_LIST, params)
        snapshots: list[SymbolPositionSnapshot] = []
        if isinstance(result, dict):
            items = result.get("list")
            if isinstance(items, list):
                for item in items:
                    symbol_raw = str(item.get("symbol", ""))
                    if not symbol_raw:
                        continue
                    display_symbol = self._resolve_display_symbol(exchange, symbol_raw)
                    snapshots.append(self._build_position_snapshot(exchange, display_symbol, item))
        return snapshots

    def _fetch_order_remote(
        self,
        order_id: str,
        *,
        symbol_hint: str | None = None,
    ) -> OrderExecutionSnapshot:
        symbol_display = self._order_symbol.get(order_id)
        if symbol_display is None:
            symbol_display = symbol_hint
        if symbol_display is None:
            raise ExchangeClientError(f"Неизвестен символ ордера {order_id}")

        params = {
            "category": self._CATEGORY,
            "symbol": _normalize_symbol_for_api(symbol_display),
            "orderId": order_id,
        }
        result = self._signed_request("GET", self._ORDER_QUERY, params)
        order_info = None
        if isinstance(result, dict):
            items = result.get("list")
            if isinstance(items, list) and items:
                order_info = items[0]
        if not isinstance(order_info, dict):
            raise ExchangeClientError(f"Bybit не вернул данные по ордеру {order_id}")

        snapshot = self._snapshot_from_payload(order_info)
        self._cache_order(snapshot, symbol_display)
        return snapshot

    def _build_position_snapshot(
        self,
        exchange: Exchange,
        symbol: str,
        position_info: dict[str, object] | None,
    ) -> SymbolPositionSnapshot:
        quantity_value = 0.0
        side: SignalDirection | None = None
        updated_at: datetime | None = None

        if isinstance(position_info, dict):
            raw_quantity = self._safe_float(position_info.get("size"))
            raw_side = str(position_info.get("side", "")).upper()
            update_time = self._safe_int(position_info.get("updatedTime"))
            if update_time:
                updated_at = self._to_datetime(update_time)
            if raw_quantity is not None:
                quantity_value = float(abs(raw_quantity))
            if raw_quantity and raw_quantity != 0:
                if raw_side == "BUY":
                    side = SignalDirection.LONG
                elif raw_side == "SELL":
                    side = SignalDirection.SHORT

        key = (exchange.value, symbol)
        orders = self._symbol_orders.get(key, {})

        entry_snapshot = self._refresh_order_if_known(orders.get("entry"))
        stop_snapshot = self._refresh_order_if_known(orders.get("stop"))
        take_snapshot = self._refresh_order_if_known(orders.get("take"))
        close_snapshot = self._refresh_order_if_known(orders.get("close"))

        stored_side = orders.get("side")
        if side is None and isinstance(stored_side, SignalDirection):
            side = stored_side

        status = PositionStatus.NONE
        if quantity_value > 0:
            status = PositionStatus.OPEN
        else:
            if close_snapshot and close_snapshot.status is OrderStatus.FILLED:
                status = PositionStatus.CLOSED
            elif stop_snapshot and stop_snapshot.status is OrderStatus.FILLED:
                status = PositionStatus.CLOSED
            elif take_snapshot and take_snapshot.status is OrderStatus.FILLED:
                status = PositionStatus.CLOSED
            elif entry_snapshot and entry_snapshot.status is OrderStatus.CANCELLED:
                status = PositionStatus.NONE
            elif entry_snapshot and entry_snapshot.status is OrderStatus.FILLED:
                status = PositionStatus.CLOSED

        if updated_at is None:
            timestamps = [
                snapshot.updated_at
                for snapshot in (entry_snapshot, stop_snapshot, take_snapshot, close_snapshot)
                if snapshot and snapshot.updated_at is not None
            ]
            updated_at = max(timestamps) if timestamps else None

        return SymbolPositionSnapshot(
            exchange=exchange,
            symbol=symbol,
            status=status,
            side=side,
            quantity=quantity_value,
            entry=entry_snapshot,
            stop=stop_snapshot,
            take=take_snapshot,
            close=close_snapshot,
            updated_at=updated_at,
        )

    def _resolve_display_symbol(self, exchange: Exchange, symbol: str) -> str:
        if not symbol:
            return symbol

        normalized = _normalize_symbol_for_api(symbol)
        for stored_symbol in self._order_symbol.values():
            if _normalize_symbol_for_api(stored_symbol) == normalized:
                return stored_symbol

        for exchange_value, stored_symbol in self._symbol_orders.keys():
            if exchange_value == exchange.value and _normalize_symbol_for_api(stored_symbol) == normalized:
                return stored_symbol

        return symbol.strip().upper()

    def _signed_request(
        self,
        method: str,
        path: str,
        params: dict[str, object],
    ) -> dict[str, object] | list[dict[str, object]] | None:
        method_upper = method.upper()
        payload = {key: value for key, value in params.items() if value is not None}
        sorted_items = dict(sorted(payload.items()))

        timestamp = str(int(time.time() * 1000))
        recv_window = str(self._recv_window) if self._recv_window else ""

        if method_upper == "GET":
            query = urlencode(sorted_items, doseq=True)
            body = query
            url = f"{self._base_url}{path}"
            if query:
                url = f"{url}?{query}"
            request_kwargs: dict[str, object] = {}
        else:
            body = json.dumps(sorted_items, separators=(",", ":"))
            url = f"{self._base_url}{path}"
            request_kwargs = {"data": body}

        signature_payload = f"{timestamp}{self._api_key}{recv_window}{body}"
        signature = hmac.new(self._api_secret, signature_payload.encode("utf-8"), hashlib.sha256).hexdigest()

        headers = {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": timestamp,
        }
        if recv_window:
            headers["X-BAPI-RECV-WINDOW"] = recv_window
        if method_upper != "GET":
            headers["Content-Type"] = "application/json"

        try:
            response = self._session.request(
                method_upper,
                url,
                headers=headers,
                timeout=self._timeout,
                **request_kwargs,
            )
            response.raise_for_status()
        except requests.RequestException as exc:  # pragma: no cover - network errors
            raise ExchangeClientError(f"Ошибка сети при обращении к Bybit API: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ExchangeClientError("Неверный формат ответа Bybit API") from exc

        if not isinstance(data, dict):
            raise ExchangeClientError("Неожиданный формат ответа Bybit API")

        ret_code = data.get("retCode")
        if ret_code not in (0, "0"):
            message = data.get("retMsg") or str(data)
            raise ExchangeClientError(f"Bybit API вернула ошибку: {message}")

        return data.get("result")

    def _snapshot_from_payload(self, payload: dict[str, object]) -> OrderExecutionSnapshot:
        order_id = str(payload.get("orderId"))
        status_raw = str(payload.get("orderStatus", "New"))
        status = self._BYBIT_STATUS_MAP.get(status_raw.upper(), OrderStatus.CANCELLED)
        filled_qty = self._safe_float(payload.get("cumExecQty")) or 0.0
        avg_fill = self._safe_float(payload.get("avgPrice"))
        if avg_fill is not None and avg_fill <= 0:
            avg_fill = None
        update_time = self._safe_int(payload.get("updatedTime"))
        updated_at = self._to_datetime(update_time) if update_time else None

        return OrderExecutionSnapshot(
            order_id=order_id,
            status=status,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill,
            updated_at=updated_at,
        )

    def _cache_order(self, snapshot: OrderExecutionSnapshot, symbol: str) -> None:
        self._order_cache[snapshot.order_id] = snapshot
        self._order_symbol[snapshot.order_id] = symbol

    def _update_symbol_order(self, order_id: str, status: OrderStatus) -> None:
        if status is not OrderStatus.CANCELLED:
            return
        for orders in self._symbol_orders.values():
            for key, value in list(orders.items()):
                if isinstance(value, str) and value == order_id:
                    orders.pop(key, None)

    def _refresh_order_if_known(self, order_id: str | None) -> OrderExecutionSnapshot | None:
        if order_id is None:
            return None
        try:
            return self._fetch_order_remote(order_id)
        except ExchangeClientError:
            return self._order_cache.get(order_id)

    @staticmethod
    def _direction_to_side(direction: SignalDirection) -> str:
        return "Buy" if direction.is_long else "Sell"

    @staticmethod
    def _opposite_side(direction: SignalDirection) -> str:
        return "Sell" if direction.is_long else "Buy"

    @staticmethod
    def _client_order_id(prefix: str, suffix: str) -> str:
        base = prefix.replace(" ", "")
        candidate = f"{base[:20]}-{suffix}"
        return candidate[:32]

    @staticmethod
    def _format_decimal(value: float) -> str:
        return ("{:.10f}".format(value)).rstrip("0").rstrip(".") or "0"

    @staticmethod
    def _safe_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_datetime(timestamp_ms: int) -> datetime:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=config.UTC).astimezone(config.TIMEZONE)

    @staticmethod
    def _extract_order_id(result: dict[str, object] | list[dict[str, object]] | None) -> str:
        if isinstance(result, dict):
            order_id = result.get("orderId")
            if order_id:
                return str(order_id)
        raise ExchangeClientError("Bybit API не вернул идентификатор ордера")

    @staticmethod
    def _trigger_direction_stop(direction: SignalDirection) -> int:
        return 2 if direction.is_long else 1

    @staticmethod
    def _trigger_direction_take(direction: SignalDirection) -> int:
        return 1 if direction.is_long else 2
