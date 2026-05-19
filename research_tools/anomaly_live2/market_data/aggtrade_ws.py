"""Binance futures aggTrade WebSocket ingestion for anomaly live2."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

from ..clock import utc_now_ms
from ..state import SymbolStateStore
from .backoff import Live2ReconnectBackoff
from .candles import Live2AggTradeEvent
from .common import optional_float, optional_int, symbol_to_market_id

BINANCE_FUTURES_COMBINED_STREAM_BASE_URL = "wss://fstream.binance.com/stream?streams="


def _aiohttp_ws_connector() -> object:
    import aiohttp

    return aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver(), ttl_dns_cache=300)


def _chunked(values: tuple[str, ...], size: int) -> tuple[tuple[str, ...], ...]:
    if size <= 0:
        raise ValueError("chunk size must be > 0")
    return tuple(tuple(values[index:index + size]) for index in range(0, len(values), size))


@dataclass(frozen=True, slots=True)
class Live2AggTradeWsShardStatus:
    shard_id: int
    connection_status: str
    last_message_at_ms: int | None
    last_error: str | None
    messages_received: int
    rows_applied: int
    rows_filtered: int
    payload_errors: int
    streams_count: int
    connect_attempts: int
    reconnect_attempts: int
    disconnect_count: int
    thread_alive: bool
    backoff_attempt: int
    last_backoff_delay_seconds: float

    def is_connected(self, *, stale_ms: int) -> bool:
        if self.connection_status != "connected":
            return False
        if self.last_message_at_ms is None:
            return True
        return max(0, utc_now_ms() - int(self.last_message_at_ms)) <= stale_ms

    def as_dict(self, *, stale_ms: int) -> dict[str, object]:
        age_ms = None if self.last_message_at_ms is None else max(0, utc_now_ms() - int(self.last_message_at_ms))
        return {
            "shard_id": self.shard_id,
            "connection_status": self.connection_status,
            "last_message_at_ms": self.last_message_at_ms,
            "last_message_age_ms": age_ms,
            "last_error": self.last_error,
            "messages_received": self.messages_received,
            "rows_applied": self.rows_applied,
            "rows_filtered": self.rows_filtered,
            "payload_errors": self.payload_errors,
            "streams_count": self.streams_count,
            "connect_attempts": self.connect_attempts,
            "reconnect_attempts": self.reconnect_attempts,
            "disconnect_count": self.disconnect_count,
            "thread_alive": self.thread_alive,
            "backoff_attempt": self.backoff_attempt,
            "last_backoff_delay_seconds": self.last_backoff_delay_seconds,
            "connected": self.is_connected(stale_ms=stale_ms),
            "stale_ms": stale_ms,
        }


@dataclass(frozen=True, slots=True)
class Live2AggTradeWsStatus:
    source_status: str
    shard_statuses: tuple[Live2AggTradeWsShardStatus, ...]
    symbols_total: int
    streams_total: int
    max_streams_per_connection: int

    def as_dict(self, *, stale_ms: int) -> dict[str, object]:
        connected = sum(1 for shard in self.shard_statuses if shard.is_connected(stale_ms=stale_ms))
        messages = sum(shard.messages_received for shard in self.shard_statuses)
        rows_applied = sum(shard.rows_applied for shard in self.shard_statuses)
        rows_filtered = sum(shard.rows_filtered for shard in self.shard_statuses)
        payload_errors = sum(shard.payload_errors for shard in self.shard_statuses)
        last_message_at_ms = max(
            (int(shard.last_message_at_ms) for shard in self.shard_statuses if shard.last_message_at_ms is not None),
            default=None,
        )
        connect_attempts = sum(shard.connect_attempts for shard in self.shard_statuses)
        reconnect_attempts = sum(shard.reconnect_attempts for shard in self.shard_statuses)
        disconnect_count = sum(shard.disconnect_count for shard in self.shard_statuses)
        ready = bool(self.shard_statuses) and connected == len(self.shard_statuses)
        return {
            "source_status": self.source_status,
            "ready": ready,
            "shards_total": len(self.shard_statuses),
            "shards_connected": connected,
            "symbols_total": self.symbols_total,
            "streams_total": self.streams_total,
            "max_streams_per_connection": self.max_streams_per_connection,
            "last_message_at_ms": last_message_at_ms,
            "last_message_age_ms": None if last_message_at_ms is None else max(0, utc_now_ms() - int(last_message_at_ms)),
            "messages_received": messages,
            "rows_applied": rows_applied,
            "rows_filtered": rows_filtered,
            "payload_errors": payload_errors,
            "connect_attempts": connect_attempts,
            "reconnect_attempts": reconnect_attempts,
            "disconnect_count": disconnect_count,
            "shards": [shard.as_dict(stale_ms=stale_ms) for shard in self.shard_statuses],
            "stale_ms": stale_ms,
        }


class Live2AggTradeWsSource:
    """Shard Binance aggTrade streams and update per-symbol candle rings.

    The source has no REST repair path and writes no per-trade artifacts. It only
    mutates SymbolStateStore in memory; heartbeat artifacts sample the state.
    """

    source_id = "binance_futures_aggtrade_ws"

    def __init__(
        self,
        *,
        state_store: SymbolStateStore,
        symbols: tuple[str, ...],
        stale_ms: int,
        startup_wait_seconds: float,
        max_streams_per_connection: int,
        reconnect_initial_delay_seconds: float = 1.0,
        reconnect_max_delay_seconds: float = 60.0,
    ) -> None:
        self.state_store = state_store
        self.symbols = tuple(dict.fromkeys(symbol.strip() for symbol in symbols if symbol.strip()))
        self.stale_ms = int(stale_ms)
        self.startup_wait_seconds = float(startup_wait_seconds)
        self.max_streams_per_connection = int(max_streams_per_connection)
        if self.max_streams_per_connection <= 0:
            raise ValueError("max_streams_per_connection must be > 0")
        self._market_id_to_symbol = self._build_market_id_filter(self.symbols)
        self._ready_event = threading.Event()
        self._shards: tuple[_Live2AggTradeWsShard, ...] = tuple(
            _Live2AggTradeWsShard(
                shard_id=index + 1,
                market_ids=market_ids,
                market_id_to_symbol=self._market_id_to_symbol,
                state_store=self.state_store,
                source_id=self.source_id,
                ready_callback=self._mark_ready_if_all_connected,
                reconnect_initial_delay_seconds=float(reconnect_initial_delay_seconds),
                reconnect_max_delay_seconds=float(reconnect_max_delay_seconds),
            )
            for index, market_ids in enumerate(_chunked(tuple(self._market_id_to_symbol), self.max_streams_per_connection))
        )

    def start(self) -> None:
        for shard in self._shards:
            shard.start()
        self._mark_ready_if_all_connected()

    def close(self) -> None:
        for shard in self._shards:
            shard.close()

    def wait_until_ready(self) -> bool:
        if not self._shards:
            return False
        return self._ready_event.wait(timeout=max(0.0, self.startup_wait_seconds))

    def is_ready(self) -> bool:
        if not self._shards:
            return False
        return all(shard.status().is_connected(stale_ms=self.stale_ms) for shard in self._shards)

    def status(self) -> Live2AggTradeWsStatus:
        if not self._shards:
            source_status = "no_symbols"
        elif self.is_ready():
            source_status = "connected"
        else:
            source_status = "partial_or_connecting"
        return Live2AggTradeWsStatus(
            source_status=source_status,
            shard_statuses=tuple(shard.status() for shard in self._shards),
            symbols_total=len(self.symbols),
            streams_total=len(self._market_id_to_symbol),
            max_streams_per_connection=self.max_streams_per_connection,
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

    def _mark_ready_if_all_connected(self) -> None:
        if self._shards and all(shard.connection_status() == "connected" for shard in self._shards):
            self._ready_event.set()


class _Live2AggTradeWsShard:
    def __init__(
        self,
        *,
        shard_id: int,
        market_ids: tuple[str, ...],
        market_id_to_symbol: dict[str, str],
        state_store: SymbolStateStore,
        source_id: str,
        ready_callback: Callable[[], None],
        reconnect_initial_delay_seconds: float = 1.0,
        reconnect_max_delay_seconds: float = 60.0,
    ) -> None:
        self.shard_id = int(shard_id)
        self.market_ids = tuple(market_ids)
        self.market_id_to_symbol = market_id_to_symbol
        self.state_store = state_store
        self.source_id = source_id
        self.ready_callback = ready_callback
        self._backoff = Live2ReconnectBackoff(
            initial_seconds=float(reconnect_initial_delay_seconds),
            max_seconds=float(reconnect_max_delay_seconds),
        )
        self._last_backoff_delay_seconds = 0.0
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._connection_status = "starting"
        self._last_message_at_ms: int | None = None
        self._last_error: str | None = None
        self._messages_received = 0
        self._rows_applied = 0
        self._rows_filtered = 0
        self._payload_errors = 0
        self._connect_attempts = 0
        self._reconnect_attempts = 0
        self._disconnect_count = 0
        self._thread = threading.Thread(
            target=self._run_thread,
            name=f"live2-binance-aggtrade-{self.shard_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def connection_status(self) -> str:
        with self._lock:
            return self._connection_status

    def status(self) -> Live2AggTradeWsShardStatus:
        with self._lock:
            return Live2AggTradeWsShardStatus(
                shard_id=self.shard_id,
                connection_status=self._connection_status,
                last_message_at_ms=self._last_message_at_ms,
                last_error=self._last_error,
                messages_received=self._messages_received,
                rows_applied=self._rows_applied,
                rows_filtered=self._rows_filtered,
                payload_errors=self._payload_errors,
                streams_count=len(self.market_ids),
                connect_attempts=self._connect_attempts,
                reconnect_attempts=self._reconnect_attempts,
                disconnect_count=self._disconnect_count,
                thread_alive=self._thread.is_alive(),
                backoff_attempt=self._backoff.attempt,
                last_backoff_delay_seconds=self._last_backoff_delay_seconds,
            )

    def _run_thread(self) -> None:
        if not self.market_ids:
            self._set_status("no_streams", "empty_shard")
            return
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
        streams = "/".join(f"{market_id.lower()}@aggTrade" for market_id in self.market_ids)
        url = BINANCE_FUTURES_COMBINED_STREAM_BASE_URL + quote(streams, safe="/@")
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.ws_connect(url, heartbeat=20, max_msg_size=8 * 1024 * 1024) as ws:
                self._set_status("connected", None)
                self._reset_backoff()
                self.ready_callback()
                while not self._stop_event.is_set():
                    try:
                        message = await ws.receive(timeout=max(1.0, self.stale_ms / 1000.0))
                    except TimeoutError:
                        if self._watchdog_stale():
                            self._set_status("watchdog_stale", "aggtrade_ws_watchdog_stale", count_disconnect=True)
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
        row = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
        if not isinstance(row, dict):
            self._record_payload_error(f"unexpected_payload:{type(payload).__name__}")
            return
        received_at_ms = utc_now_ms()
        trade = self._parse_trade(row)
        if trade is None:
            with self._lock:
                self._messages_received += 1
                self._rows_filtered += 1
                self._last_message_at_ms = received_at_ms
            return
        self.state_store.update_aggtrade(trade, received_at_ms=received_at_ms)
        with self._lock:
            self._messages_received += 1
            self._rows_applied += 1
            self._last_message_at_ms = received_at_ms
            self._last_error = None
            self._connection_status = "connected"

    def _parse_trade(self, row: dict[str, Any]) -> Live2AggTradeEvent | None:
        market_id = str(row.get("s", "")).strip().upper()
        symbol = self.market_id_to_symbol.get(market_id)
        if not market_id or symbol is None:
            return None
        price = optional_float(row.get("p"))
        quantity = optional_float(row.get("q"))
        trade_time_ms = optional_int(row.get("T")) or optional_int(row.get("E"))
        if price is None or quantity is None or price <= 0.0 or quantity <= 0.0 or trade_time_ms is None:
            return None
        quote_quantity = price * quantity
        buyer_is_maker = bool(row.get("m", False))
        taker_buy_quote_quantity = 0.0 if buyer_is_maker else quote_quantity
        return Live2AggTradeEvent(
            symbol=symbol,
            market_id=market_id,
            aggregate_trade_id=optional_int(row.get("a")),
            event_time_ms=optional_int(row.get("E")),
            trade_time_ms=int(trade_time_ms),
            price=price,
            quantity=quantity,
            quote_quantity=quote_quantity,
            taker_buy_quote_quantity=taker_buy_quote_quantity,
            buyer_is_maker=buyer_is_maker,
            source=self.source_id,
        )

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
