"""Binance USD-M futures private user-data stream for anomaly live2.

This source is intentionally execution-only: it does not place orders and does
not mutate local protected positions. It keeps the Binance listenKey alive,
maintains a private WebSocket connection, and emits order/account lifecycle
payloads into live2 artifacts so real fills, stop rejects, and position updates
are visible outside REST polling.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from .clock import utc_now_ms
from .contracts import Live2Component, Live2Event, Live2Severity
from .market_data.backoff import Live2ReconnectBackoff
from .market_data.common import market_id_to_default_symbol

BINANCE_FUTURES_PRIVATE_WS_BASE_URL = "wss://fstream.binance.com/private/ws"
BINANCE_FUTURES_PRIVATE_ENDPOINT_CATEGORY = "private"
LISTEN_KEY_TTL_SECONDS = 60 * 60


@runtime_checkable
class Live2UserDataExchange(Protocol):
    """Exchange boundary required by the live2 private user-data stream."""

    def create_futures_user_data_listen_key(self) -> str:
        """Create or refresh a Binance USD-M futures listenKey."""
        ...

    def keepalive_futures_user_data_listen_key(self, listen_key: str) -> None:
        """Extend a Binance USD-M futures listenKey validity."""
        ...

    def close_futures_user_data_listen_key(self, listen_key: str) -> None:
        """Invalidate a Binance USD-M futures listenKey."""
        ...


@dataclass(frozen=True, slots=True)
class Live2UserDataOrderEvent:
    event_time_ms: int | None
    transaction_time_ms: int | None
    symbol: str
    market_id: str
    client_order_id: str
    order_id: str
    side: str
    order_type: str
    execution_type: str
    order_status: str
    last_filled_quantity: float | None
    cumulative_filled_quantity: float | None
    last_filled_price: float | None
    average_price: float | None
    realized_profit: float | None
    reduce_only: bool | None
    raw: dict[str, object] = field(repr=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "event_time_ms": self.event_time_ms,
            "transaction_time_ms": self.transaction_time_ms,
            "symbol": self.symbol,
            "market_id": self.market_id,
            "client_order_id": self.client_order_id,
            "order_id": self.order_id,
            "side": self.side,
            "order_type": self.order_type,
            "execution_type": self.execution_type,
            "order_status": self.order_status,
            "last_filled_quantity": self.last_filled_quantity,
            "cumulative_filled_quantity": self.cumulative_filled_quantity,
            "last_filled_price": self.last_filled_price,
            "average_price": self.average_price,
            "realized_profit": self.realized_profit,
            "reduce_only": self.reduce_only,
            "raw": self.raw,
        }


@dataclass(frozen=True, slots=True)
class Live2UserDataStatus:
    source_id: str
    status: str
    ready: bool
    reason: str
    endpoint_category: str
    websocket_url_redacted: str
    listen_key_status: str
    listen_key_created_at_ms: int | None
    listen_key_last_keepalive_at_ms: int | None
    listen_key_expires_at_ms: int | None
    keepalive_interval_seconds: float
    connection_started_at_ms: int | None
    first_message_at_ms: int | None
    last_message_at_ms: int | None
    current_connection_age_seconds: float | None
    connection_max_age_seconds: float
    last_message_age_ms: int | None
    last_error: str | None
    last_close_code: int | None
    last_close_reason: str | None
    last_exception_type: str | None
    last_exception_text: str | None
    connect_attempts: int
    reconnect_attempts: int
    disconnect_count: int
    planned_rotation_count: int
    listen_key_create_count: int
    listen_key_keepalive_count: int
    listen_key_keepalive_errors: int
    listen_key_recreate_count: int
    messages_received: int
    payload_errors: int
    events_by_type: dict[str, int]
    order_events_received: int
    account_update_events_received: int
    conditional_reject_events_received: int
    last_order_event: dict[str, object] | None
    recent_order_events: list[dict[str, object]]
    thread_alive: bool
    backoff_attempt: int
    last_backoff_delay_seconds: float

    def as_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "status": self.status,
            "ready": self.ready,
            "reason": self.reason,
            "endpoint_category": self.endpoint_category,
            "websocket_url_redacted": self.websocket_url_redacted,
            "listen_key_status": self.listen_key_status,
            "listen_key_created_at_ms": self.listen_key_created_at_ms,
            "listen_key_last_keepalive_at_ms": self.listen_key_last_keepalive_at_ms,
            "listen_key_expires_at_ms": self.listen_key_expires_at_ms,
            "keepalive_interval_seconds": self.keepalive_interval_seconds,
            "connection_started_at_ms": self.connection_started_at_ms,
            "first_message_at_ms": self.first_message_at_ms,
            "last_message_at_ms": self.last_message_at_ms,
            "current_connection_age_seconds": self.current_connection_age_seconds,
            "connection_max_age_seconds": self.connection_max_age_seconds,
            "last_message_age_ms": self.last_message_age_ms,
            "last_error": self.last_error,
            "last_close_code": self.last_close_code,
            "last_close_reason": self.last_close_reason,
            "last_exception_type": self.last_exception_type,
            "last_exception_text": self.last_exception_text,
            "connect_attempts": self.connect_attempts,
            "reconnect_attempts": self.reconnect_attempts,
            "disconnect_count": self.disconnect_count,
            "planned_rotation_count": self.planned_rotation_count,
            "listen_key_create_count": self.listen_key_create_count,
            "listen_key_keepalive_count": self.listen_key_keepalive_count,
            "listen_key_keepalive_errors": self.listen_key_keepalive_errors,
            "listen_key_recreate_count": self.listen_key_recreate_count,
            "messages_received": self.messages_received,
            "payload_errors": self.payload_errors,
            "events_by_type": dict(self.events_by_type),
            "order_events_received": self.order_events_received,
            "account_update_events_received": self.account_update_events_received,
            "conditional_reject_events_received": self.conditional_reject_events_received,
            "last_order_event": self.last_order_event,
            "recent_order_events": list(self.recent_order_events),
            "thread_alive": self.thread_alive,
            "backoff_attempt": self.backoff_attempt,
            "last_backoff_delay_seconds": self.last_backoff_delay_seconds,
        }


class Live2UserDataStreamSource:
    """Maintain Binance private user-data WS health and audit order events."""

    source_id = "binance_futures_private_user_data_ws"
    endpoint_category = BINANCE_FUTURES_PRIVATE_ENDPOINT_CATEGORY
    websocket_url_redacted = f"{BINANCE_FUTURES_PRIVATE_WS_BASE_URL}/<listenKey>"

    def __init__(
        self,
        *,
        exchange_client: object | None,
        startup_wait_seconds: float,
        keepalive_interval_seconds: float = 1_800.0,
        reconnect_initial_delay_seconds: float = 1.0,
        reconnect_max_delay_seconds: float = 60.0,
        connection_max_age_seconds: float = 84_600.0,
        event_callback: Callable[[Live2Event], None] | None = None,
    ) -> None:
        if keepalive_interval_seconds <= 0:
            raise ValueError("keepalive_interval_seconds must be > 0")
        if connection_max_age_seconds <= 0:
            raise ValueError("connection_max_age_seconds must be > 0")
        self.exchange_client = exchange_client
        self.startup_wait_seconds = float(startup_wait_seconds)
        self.keepalive_interval_seconds = float(keepalive_interval_seconds)
        self.connection_max_age_seconds = float(connection_max_age_seconds)
        self.event_callback = event_callback
        self._backoff = Live2ReconnectBackoff(
            initial_seconds=float(reconnect_initial_delay_seconds),
            max_seconds=float(reconnect_max_delay_seconds),
        )
        self._last_backoff_delay_seconds = 0.0
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._connection_status = "starting"
        self._listen_key_status = "not_created"
        self._listen_key: str | None = None
        self._listen_key_created_at_ms: int | None = None
        self._listen_key_last_keepalive_at_ms: int | None = None
        self._listen_key_expires_at_ms: int | None = None
        self._connection_started_at_ms: int | None = None
        self._first_message_at_ms: int | None = None
        self._last_message_at_ms: int | None = None
        self._last_error: str | None = None
        self._last_close_code: int | None = None
        self._last_close_reason: str | None = None
        self._last_exception_type: str | None = None
        self._last_exception_text: str | None = None
        self._connect_attempts = 0
        self._reconnect_attempts = 0
        self._disconnect_count = 0
        self._planned_rotation_count = 0
        self._listen_key_create_count = 0
        self._listen_key_keepalive_count = 0
        self._listen_key_keepalive_errors = 0
        self._listen_key_recreate_count = 0
        self._messages_received = 0
        self._payload_errors = 0
        self._events_by_type: Counter[str] = Counter()
        self._order_events_received = 0
        self._account_update_events_received = 0
        self._conditional_reject_events_received = 0
        self._last_order_event: dict[str, object] | None = None
        self._recent_order_events: deque[dict[str, object]] = deque(maxlen=200)
        self._thread = threading.Thread(target=self._run_thread, name="live2-binance-user-data", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
        listen_key = self._listen_key_snapshot()
        if listen_key and isinstance(self.exchange_client, Live2UserDataExchange):
            try:
                self.exchange_client.close_futures_user_data_listen_key(listen_key)
            except Exception:
                # Shutdown cleanup must not hide the primary live2 stop reason.
                pass

    def wait_until_ready(self) -> bool:
        return self._ready_event.wait(timeout=max(0.0, self.startup_wait_seconds))

    def is_ready(self) -> bool:
        return self.status().ready

    def status(self) -> Live2UserDataStatus:
        now_ms = utc_now_ms()
        with self._lock:
            connection_age_seconds = None
            if self._connection_started_at_ms is not None:
                connection_age_seconds = max(0.0, (now_ms - int(self._connection_started_at_ms)) / 1000.0)
            last_message_age_ms = None
            if self._last_message_at_ms is not None:
                last_message_age_ms = max(0, now_ms - int(self._last_message_at_ms))
            listen_key_valid = self._listen_key_expires_at_ms is not None and int(self._listen_key_expires_at_ms) > now_ms
            boundary_ok = isinstance(self.exchange_client, Live2UserDataExchange)
            ready = (
                boundary_ok
                and self._connection_status == "connected"
                and self._listen_key_status == "active"
                and listen_key_valid
                and self._thread.is_alive()
            )
            if not boundary_ok:
                status = "not_ready"
                reason = "exchange_client_missing_user_data_stream_boundary"
            elif ready:
                status = "ready"
                reason = "private_user_data_stream_connected_and_listen_key_active"
            else:
                status = "not_ready"
                reason = self._last_error or self._listen_key_status or self._connection_status
            return Live2UserDataStatus(
                source_id=self.source_id,
                status=status,
                ready=ready,
                reason=reason,
                endpoint_category=self.endpoint_category,
                websocket_url_redacted=self.websocket_url_redacted,
                listen_key_status=self._listen_key_status,
                listen_key_created_at_ms=self._listen_key_created_at_ms,
                listen_key_last_keepalive_at_ms=self._listen_key_last_keepalive_at_ms,
                listen_key_expires_at_ms=self._listen_key_expires_at_ms,
                keepalive_interval_seconds=self.keepalive_interval_seconds,
                connection_started_at_ms=self._connection_started_at_ms,
                first_message_at_ms=self._first_message_at_ms,
                last_message_at_ms=self._last_message_at_ms,
                current_connection_age_seconds=connection_age_seconds,
                connection_max_age_seconds=self.connection_max_age_seconds,
                last_message_age_ms=last_message_age_ms,
                last_error=self._last_error,
                last_close_code=self._last_close_code,
                last_close_reason=self._last_close_reason,
                last_exception_type=self._last_exception_type,
                last_exception_text=self._last_exception_text,
                connect_attempts=self._connect_attempts,
                reconnect_attempts=self._reconnect_attempts,
                disconnect_count=self._disconnect_count,
                planned_rotation_count=self._planned_rotation_count,
                listen_key_create_count=self._listen_key_create_count,
                listen_key_keepalive_count=self._listen_key_keepalive_count,
                listen_key_keepalive_errors=self._listen_key_keepalive_errors,
                listen_key_recreate_count=self._listen_key_recreate_count,
                messages_received=self._messages_received,
                payload_errors=self._payload_errors,
                events_by_type=dict(self._events_by_type),
                order_events_received=self._order_events_received,
                account_update_events_received=self._account_update_events_received,
                conditional_reject_events_received=self._conditional_reject_events_received,
                last_order_event=self._last_order_event,
                recent_order_events=list(self._recent_order_events),
                thread_alive=self._thread.is_alive(),
                backoff_attempt=self._backoff.attempt,
                last_backoff_delay_seconds=self._last_backoff_delay_seconds,
            )

    def _run_thread(self) -> None:
        if not isinstance(self.exchange_client, Live2UserDataExchange):
            self._set_status("not_ready", "exchange_client_missing_user_data_stream_boundary")
            return
        while not self._stop_event.is_set():
            self._record_connect_attempt()
            try:
                listen_key = self._ensure_listen_key()
            except Exception as exc:
                self._set_status("error", f"listen_key_create_failed:{type(exc).__name__}:{exc}", exception=exc)
                self._sleep_backoff()
                continue
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                outcome = loop.run_until_complete(self._run_ws_loop(listen_key))
            except Exception as exc:  # pragma: no cover - network boundary
                outcome = "error"
                self._set_status("error", f"{type(exc).__name__}: {exc}", count_disconnect=True, exception=exc)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
                asyncio.set_event_loop(None)
            if self._stop_event.is_set():
                break
            if outcome == "planned_rotation":
                self._last_backoff_delay_seconds = 0.0
                continue
            self._sleep_backoff()
        self._set_status("stopped", "user_data_stream_stopped")

    def _ensure_listen_key(self) -> str:
        now_ms = utc_now_ms()
        with self._lock:
            current = self._listen_key
            expires = self._listen_key_expires_at_ms or 0
        if current and expires - now_ms > 120_000:
            return current
        assert isinstance(self.exchange_client, Live2UserDataExchange)
        listen_key = self.exchange_client.create_futures_user_data_listen_key()
        if not isinstance(listen_key, str) or not listen_key.strip():
            raise RuntimeError("empty listenKey returned by exchange")
        listen_key = listen_key.strip()
        with self._lock:
            if self._listen_key and self._listen_key != listen_key:
                self._listen_key_recreate_count += 1
            self._listen_key = listen_key
            self._listen_key_status = "active"
            self._listen_key_created_at_ms = now_ms
            self._listen_key_last_keepalive_at_ms = now_ms
            self._listen_key_expires_at_ms = now_ms + LISTEN_KEY_TTL_SECONDS * 1000
            self._listen_key_create_count += 1
            self._last_error = None
        self._emit_event(
            Live2Event(
                event_type="user_data_listen_key_created",
                component=Live2Component.EXECUTION,
                severity=Live2Severity.INFO,
                message="Binance futures user-data listenKey created or extended",
                data={
                    "source": self.source_id,
                    "endpoint_category": self.endpoint_category,
                    "websocket_url_redacted": self.websocket_url_redacted,
                    "listen_key_redacted": _redact_listen_key(listen_key),
                    "listen_key_expires_at_ms": now_ms + LISTEN_KEY_TTL_SECONDS * 1000,
                },
            )
        )
        return listen_key

    async def _run_ws_loop(self, listen_key: str) -> str:
        import aiohttp

        url = f"{BINANCE_FUTURES_PRIVATE_WS_BASE_URL}/{listen_key}"
        timeout = aiohttp.ClientTimeout(total=None, connect=20, sock_read=None)
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver(), ttl_dns_cache=300)
        connection_started_at_ms = utc_now_ms()
        next_keepalive_monotonic = time.monotonic() + min(self.keepalive_interval_seconds, 1_800.0)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.ws_connect(url, heartbeat=20, max_msg_size=8 * 1024 * 1024) as ws:
                self._set_status("connected", None, connection_started_at_ms=connection_started_at_ms)
                self._ready_event.set()
                self._backoff.reset()
                while not self._stop_event.is_set():
                    if self._connection_age_exceeded(connection_started_at_ms):
                        self._record_planned_rotation(reason="connection_max_age_exceeded")
                        await ws.close(code=1000, message=b"planned live2 user-data rotation")
                        return "planned_rotation"
                    if time.monotonic() >= next_keepalive_monotonic:
                        await self._keepalive_current_listen_key(listen_key)
                        next_keepalive_monotonic = time.monotonic() + self.keepalive_interval_seconds
                    try:
                        msg = await ws.receive(timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self._handle_message(msg.data)
                        continue
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        try:
                            self._handle_message(msg.data.decode("utf-8"))
                        except Exception as exc:
                            self._record_payload_error(f"binary_decode_failed:{type(exc).__name__}:{exc}")
                        continue
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                        self._set_status(
                            "disconnected",
                            "websocket_closed",
                            count_disconnect=True,
                            close_code=ws.close_code,
                            close_reason=getattr(ws, "close_reason", None),
                        )
                        return "closed"
                    if msg.type == aiohttp.WSMsgType.ERROR:
                        exc = ws.exception()
                        self._set_status(
                            "error",
                            f"websocket_error:{exc}",
                            count_disconnect=True,
                            close_code=ws.close_code,
                            close_reason=getattr(ws, "close_reason", None),
                            exception=exc if isinstance(exc, Exception) else None,
                        )
                        return "error"
        return "closed"

    async def _keepalive_current_listen_key(self, listen_key: str) -> None:
        try:
            assert isinstance(self.exchange_client, Live2UserDataExchange)
            await asyncio.to_thread(self.exchange_client.keepalive_futures_user_data_listen_key, listen_key)
        except Exception as exc:
            with self._lock:
                self._listen_key_keepalive_errors += 1
                self._listen_key_status = "keepalive_failed"
                self._last_error = f"listen_key_keepalive_failed:{type(exc).__name__}:{exc}"
                self._last_exception_type = type(exc).__name__
                self._last_exception_text = str(exc)[:500]
            self._emit_event(
                Live2Event(
                    event_type="user_data_listen_key_keepalive_failed",
                    component=Live2Component.EXECUTION,
                    severity=Live2Severity.ERROR,
                    message="Binance futures user-data listenKey keepalive failed",
                    data={"source": self.source_id, "reason": self._last_error},
                )
            )
            raise
        now_ms = utc_now_ms()
        with self._lock:
            self._listen_key_status = "active"
            self._listen_key_last_keepalive_at_ms = now_ms
            self._listen_key_expires_at_ms = now_ms + LISTEN_KEY_TTL_SECONDS * 1000
            self._listen_key_keepalive_count += 1
            self._last_error = None

    def _handle_message(self, raw_text: str) -> None:
        now_ms = utc_now_ms()
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            self._record_payload_error(f"json_decode_failed:{exc}")
            return
        if not isinstance(payload, dict):
            self._record_payload_error("payload_is_not_object")
            return
        event_type = str(payload.get("e") or "unknown")
        with self._lock:
            self._messages_received += 1
            self._events_by_type[event_type] += 1
            self._last_message_at_ms = now_ms
            if self._first_message_at_ms is None:
                self._first_message_at_ms = now_ms
        if event_type == "ORDER_TRADE_UPDATE":
            self._handle_order_trade_update(payload)
        elif event_type == "ACCOUNT_UPDATE":
            with self._lock:
                self._account_update_events_received += 1
            self._emit_user_payload_event("user_data_account_update", payload, severity=Live2Severity.INFO)
        elif event_type == "CONDITIONAL_ORDER_TRIGGER_REJECT":
            with self._lock:
                self._conditional_reject_events_received += 1
            self._emit_user_payload_event("user_data_conditional_order_trigger_reject", payload, severity=Live2Severity.ERROR)
        elif event_type in {"listenKeyExpired", "USER_DATA_STREAM_EXPIRED"}:
            with self._lock:
                self._listen_key_status = "expired"
                self._listen_key_expires_at_ms = now_ms
            self._emit_user_payload_event("user_data_listen_key_expired", payload, severity=Live2Severity.ERROR)
        else:
            self._emit_user_payload_event("user_data_event", payload, severity=Live2Severity.INFO)

    def _handle_order_trade_update(self, payload: dict[str, object]) -> None:
        parsed = _parse_order_trade_update(payload)
        row = parsed.as_dict()
        with self._lock:
            self._order_events_received += 1
            self._last_order_event = row
            self._recent_order_events.append(row)
        severity = Live2Severity.ERROR if parsed.order_status in {"EXPIRED", "EXPIRED_IN_MATCH"} else Live2Severity.INFO
        self._emit_event(
            Live2Event(
                event_type="user_data_order_trade_update",
                component=Live2Component.EXECUTION,
                severity=severity,
                symbol=parsed.symbol,
                message=f"{parsed.execution_type}/{parsed.order_status} {parsed.side} {parsed.order_type}",
                data={"source": self.source_id, "order_event": row},
            )
        )

    def _emit_user_payload_event(self, event_type: str, payload: dict[str, object], *, severity: Live2Severity) -> None:
        market_id = _payload_market_id(payload)
        symbol = market_id_to_default_symbol(market_id) if market_id else ""
        self._emit_event(
            Live2Event(
                event_type=event_type,
                component=Live2Component.EXECUTION,
                severity=severity,
                symbol=symbol,
                message=str(payload.get("e") or event_type),
                data={"source": self.source_id, "payload": payload},
            )
        )

    def _emit_event(self, event: Live2Event) -> None:
        if self.event_callback is None:
            return
        try:
            self.event_callback(event)
        except Exception:
            # Artifact writer backpressure is handled by the writer readiness gate;
            # the WS thread must not die while reporting diagnostics.
            pass

    def _record_connect_attempt(self) -> None:
        with self._lock:
            self._connect_attempts += 1
            if self._connect_attempts > 1:
                self._reconnect_attempts += 1
            self._connection_status = "connecting"
            self._last_error = None
            self._last_close_code = None
            self._last_close_reason = None

    def _set_status(
        self,
        status: str,
        error: str | None,
        *,
        connection_started_at_ms: int | None = None,
        count_disconnect: bool = False,
        close_code: int | None = None,
        close_reason: str | None = None,
        exception: BaseException | None = None,
    ) -> None:
        with self._lock:
            self._connection_status = status
            if connection_started_at_ms is not None:
                self._connection_started_at_ms = connection_started_at_ms
            if count_disconnect:
                self._disconnect_count += 1
            self._last_error = error
            self._last_close_code = close_code
            self._last_close_reason = close_reason
            if exception is not None:
                self._last_exception_type = type(exception).__name__
                self._last_exception_text = str(exception)[:500]

    def _record_planned_rotation(self, *, reason: str) -> None:
        with self._lock:
            self._planned_rotation_count += 1
            self._connection_status = "planned_rotation"
            self._last_error = reason

    def _record_payload_error(self, reason: str) -> None:
        with self._lock:
            self._payload_errors += 1
            self._last_error = reason
        self._emit_event(
            Live2Event(
                event_type="user_data_payload_error",
                component=Live2Component.EXECUTION,
                severity=Live2Severity.ERROR,
                message=reason,
                data={"source": self.source_id},
            )
        )

    def _sleep_backoff(self) -> None:
        delay = self._backoff.next_delay_seconds()
        self._last_backoff_delay_seconds = delay
        self._stop_event.wait(delay)

    def _connection_age_exceeded(self, connection_started_at_ms: int) -> bool:
        return (utc_now_ms() - int(connection_started_at_ms)) >= int(self.connection_max_age_seconds * 1000)

    def _listen_key_snapshot(self) -> str | None:
        with self._lock:
            return self._listen_key


def _parse_order_trade_update(payload: dict[str, object]) -> Live2UserDataOrderEvent:
    order = payload.get("o")
    order_dict = order if isinstance(order, dict) else {}
    market_id = str(order_dict.get("s") or "")
    return Live2UserDataOrderEvent(
        event_time_ms=_optional_int(payload.get("E")),
        transaction_time_ms=_optional_int(payload.get("T") or order_dict.get("T")),
        symbol=market_id_to_default_symbol(market_id) if market_id else "",
        market_id=market_id,
        client_order_id=str(order_dict.get("c") or ""),
        order_id=str(order_dict.get("i") or ""),
        side=str(order_dict.get("S") or ""),
        order_type=str(order_dict.get("o") or ""),
        execution_type=str(order_dict.get("x") or ""),
        order_status=str(order_dict.get("X") or ""),
        last_filled_quantity=_optional_float(order_dict.get("l")),
        cumulative_filled_quantity=_optional_float(order_dict.get("z")),
        last_filled_price=_optional_float(order_dict.get("L")),
        average_price=_optional_float(order_dict.get("ap")),
        realized_profit=_optional_float(order_dict.get("rp")),
        reduce_only=_optional_bool(order_dict.get("R")),
        raw=payload,
    )


def _payload_market_id(payload: dict[str, object]) -> str:
    value = payload.get("s")
    if value:
        return str(value)
    order = payload.get("o")
    if isinstance(order, dict) and order.get("s"):
        return str(order.get("s"))
    account = payload.get("a")
    if isinstance(account, dict):
        positions = account.get("P")
        if isinstance(positions, list) and positions:
            first = positions[0]
            if isinstance(first, dict) and first.get("s"):
                return str(first.get("s"))
    return ""


def _optional_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _optional_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)):
        if int(value) == 1:
            return True
        if int(value) == 0:
            return False
    return None


def _redact_listen_key(listen_key: str) -> str:
    if len(listen_key) <= 12:
        return "<redacted>"
    return f"{listen_key[:6]}...{listen_key[-6:]}"
