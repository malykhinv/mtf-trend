"""Binance futures all-ticker WebSocket ingestion for anomaly live2."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from ..clock import utc_now_ms
from ..state import SymbolStateStore
from .backoff import Live2ReconnectBackoff
from .common import market_id_to_default_symbol, optional_float, optional_int, symbol_to_market_id

BINANCE_FUTURES_ALL_TICKER_WS_URL = "wss://fstream.binance.com/market/ws/!ticker@arr"


def _aiohttp_ws_connector() -> object:
    import aiohttp

    return aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver(), ttl_dns_cache=300)



@dataclass(frozen=True, slots=True)
class Live2TickerWsStatus:
    connection_status: str
    last_message_at_ms: int | None
    last_error: str | None
    messages_received: int
    rows_received: int
    rows_applied: int
    rows_filtered: int
    payload_errors: int
    tracked_symbols: int
    accept_all_symbols: bool
    connect_attempts: int
    reconnect_attempts: int
    disconnect_count: int
    thread_alive: bool
    backoff_attempt: int
    last_backoff_delay_seconds: float

    def as_dict(self, *, stale_ms: int) -> dict[str, object]:
        now_ms = utc_now_ms()
        age_ms = None if self.last_message_at_ms is None else max(0, now_ms - int(self.last_message_at_ms))
        ready = self.connection_status == "connected" and age_ms is not None and age_ms <= stale_ms
        return {
            "connection_status": self.connection_status,
            "last_message_at_ms": self.last_message_at_ms,
            "last_message_age_ms": age_ms,
            "last_error": self.last_error,
            "messages_received": self.messages_received,
            "rows_received": self.rows_received,
            "rows_applied": self.rows_applied,
            "rows_filtered": self.rows_filtered,
            "payload_errors": self.payload_errors,
            "tracked_symbols": self.tracked_symbols,
            "accept_all_symbols": self.accept_all_symbols,
            "connect_attempts": self.connect_attempts,
            "reconnect_attempts": self.reconnect_attempts,
            "disconnect_count": self.disconnect_count,
            "thread_alive": self.thread_alive,
            "backoff_attempt": self.backoff_attempt,
            "last_backoff_delay_seconds": self.last_backoff_delay_seconds,
            "ready": ready,
            "stale_ms": stale_ms,
        }


class Live2TickerWsSource:
    """Maintain latest 24h ticker snapshots in SymbolStateStore.

    This source deliberately updates one mutable state record per symbol. It does
    not create candidate queues, does not fetch REST data, and does not infer any
    missing flow fields.
    """

    source_id = "binance_futures_all_ticker_ws"

    def __init__(
        self,
        *,
        state_store: SymbolStateStore,
        symbols: tuple[str, ...],
        stale_ms: int,
        startup_wait_seconds: float,
        reconnect_initial_delay_seconds: float = 1.0,
        reconnect_max_delay_seconds: float = 60.0,
    ) -> None:
        self.state_store = state_store
        self.stale_ms = int(stale_ms)
        self.startup_wait_seconds = float(startup_wait_seconds)
        self._backoff = Live2ReconnectBackoff(
            initial_seconds=float(reconnect_initial_delay_seconds),
            max_seconds=float(reconnect_max_delay_seconds),
        )
        self._last_backoff_delay_seconds = 0.0
        self._market_id_to_symbol = self._build_market_id_filter(symbols)
        self._accept_all_symbols = not self._market_id_to_symbol
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._connection_status = "starting"
        self._last_message_at_ms: int | None = None
        self._last_error: str | None = None
        self._messages_received = 0
        self._rows_received = 0
        self._rows_applied = 0
        self._rows_filtered = 0
        self._payload_errors = 0
        self._connect_attempts = 0
        self._reconnect_attempts = 0
        self._disconnect_count = 0
        self._thread = threading.Thread(target=self._run_thread, name="live2-binance-all-ticker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def wait_until_ready(self) -> bool:
        return self._ready_event.wait(timeout=max(0.0, self.startup_wait_seconds))

    def is_ready(self) -> bool:
        status = self.status()
        age_ms = None
        if status.last_message_at_ms is not None:
            age_ms = max(0, utc_now_ms() - int(status.last_message_at_ms))
        return status.connection_status == "connected" and age_ms is not None and age_ms <= self.stale_ms

    def status(self) -> Live2TickerWsStatus:
        with self._lock:
            return Live2TickerWsStatus(
                connection_status=self._connection_status,
                last_message_at_ms=self._last_message_at_ms,
                last_error=self._last_error,
                messages_received=self._messages_received,
                rows_received=self._rows_received,
                rows_applied=self._rows_applied,
                rows_filtered=self._rows_filtered,
                payload_errors=self._payload_errors,
                tracked_symbols=len(self._market_id_to_symbol),
                accept_all_symbols=self._accept_all_symbols,
                connect_attempts=self._connect_attempts,
                reconnect_attempts=self._reconnect_attempts,
                disconnect_count=self._disconnect_count,
                thread_alive=self._thread.is_alive(),
                backoff_attempt=self._backoff.attempt,
                last_backoff_delay_seconds=self._last_backoff_delay_seconds,
            )

    @staticmethod
    def _build_market_id_filter(symbols: tuple[str, ...]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for symbol in symbols:
            cleaned = symbol.strip()
            market_id = symbol_to_market_id(cleaned)
            if cleaned and market_id:
                mapping[market_id] = cleaned
        return mapping

    def _run_thread(self) -> None:
        while not self._stop_event.is_set():
            self._record_connect_attempt()
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._run_ws_loop())
            except Exception as exc:  # pragma: no cover - network boundary
                self._set_status("error", f"{type(exc).__name__}: {exc}", count_disconnect=True)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
            if not self._stop_event.is_set():
                delay = self._next_backoff_delay()
                self._stop_event.wait(timeout=delay)

    async def _run_ws_loop(self) -> None:
        import aiohttp

        self._set_status("connecting", None)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
        connector = _aiohttp_ws_connector()
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.ws_connect(BINANCE_FUTURES_ALL_TICKER_WS_URL, heartbeat=20) as ws:
                self._set_status("connected", None)
                self._reset_backoff()
                while not self._stop_event.is_set():
                    try:
                        message = await ws.receive(timeout=max(1.0, self.stale_ms / 1000.0))
                    except TimeoutError:
                        if self._watchdog_stale():
                            self._set_status("watchdog_stale", "ticker_ws_watchdog_stale", count_disconnect=True)
                            await ws.close()
                            return
                        continue
                    if message.type == aiohttp.WSMsgType.TEXT:
                        self._handle_ws_payload(message.data)
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                        self._set_status("closed", "ws_closed", count_disconnect=True)
                        return
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        self._set_status("error", f"ws_error:{ws.exception()}", count_disconnect=True)
                        return

    def _handle_ws_payload(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._record_payload_error(f"json:{exc}")
            return
        rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            self._record_payload_error(f"unexpected_payload:{type(payload).__name__}")
            return
        now_ms = utc_now_ms()
        rows_received = 0
        rows_applied = 0
        rows_filtered = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            rows_received += 1
            applied = self._handle_ticker_row(row, now_ms=now_ms)
            if applied:
                rows_applied += 1
            else:
                rows_filtered += 1
        with self._lock:
            self._messages_received += 1
            self._rows_received += rows_received
            self._rows_applied += rows_applied
            self._rows_filtered += rows_filtered
            self._last_message_at_ms = now_ms
            self._last_error = None
            self._connection_status = "connected"
            if rows_applied > 0:
                self._ready_event.set()

    def _handle_ticker_row(self, row: dict[str, Any], *, now_ms: int) -> bool:
        market_id = str(row.get("s", "")).strip().upper()
        if not market_id:
            return False
        if self._accept_all_symbols:
            symbol = market_id_to_default_symbol(market_id)
        else:
            symbol = self._market_id_to_symbol.get(market_id)
            if symbol is None:
                return False
        last_price = optional_float(row.get("c"))
        quote_volume_24h = optional_float(row.get("q"))
        trade_count_24h = optional_int(row.get("n"))
        price_change_pct_24h = optional_float(row.get("P"))
        status = "ok" if last_price is not None and quote_volume_24h is not None else "missing_fields"
        reason = "" if status == "ok" else "ws_ticker_missing_last_or_quote_volume"
        self.state_store.update_ticker(
            symbol=symbol,
            market_id=market_id,
            fetched_at_ms=now_ms,
            last_price=last_price,
            quote_volume_24h=quote_volume_24h,
            trade_count_24h=trade_count_24h,
            price_change_pct_24h=price_change_pct_24h,
            source=self.source_id,
            status=status,
            reason=reason,
        )
        return True

    def _watchdog_stale(self) -> bool:
        with self._lock:
            if self._last_message_at_ms is None:
                return True
            return max(0, utc_now_ms() - int(self._last_message_at_ms)) > self.stale_ms

    def _next_backoff_delay(self) -> float:
        delay = self._backoff.next_delay_seconds()
        with self._lock:
            self._last_backoff_delay_seconds = float(delay)
        return delay

    def _reset_backoff(self) -> None:
        self._backoff.reset()
        with self._lock:
            self._last_backoff_delay_seconds = 0.0

    def _record_connect_attempt(self) -> None:
        with self._lock:
            self._connect_attempts += 1
            if self._connect_attempts > 1:
                self._reconnect_attempts += 1

    def _set_status(self, status: str, error: str | None, *, count_disconnect: bool = False) -> None:
        with self._lock:
            self._connection_status = status
            self._last_error = error
            if count_disconnect:
                self._disconnect_count += 1

    def _record_payload_error(self, error: str) -> None:
        with self._lock:
            self._payload_errors += 1
            self._last_error = error
            self._connection_status = "payload_error"
