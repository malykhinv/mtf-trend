from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    Generic,
    Iterable,
    Iterator,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)

from urllib.error import URLError
from urllib.request import urlopen

from config.config import CONFIG
from config.timezone import CURRENT_TIMEZONE
from data.logger import LogSink
from domain.models import Exchange, OrderBookLevel, OrderBookSnapshot, OrderBookUpdate, Side, SymbolFilters, Trade
from config.models.trading_profile import TradingProfile

from .events import ResyncReason, StreamEvent, StreamEventType
from .limits import StreamLimits, load_stream_limits
from .stream_buffer import StreamBuffer

try:  # pragma: no cover - imported lazily for environments without websockets
    import websockets
    from websockets import WebSocketClientProtocol
    from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK
except Exception:  # pragma: no cover - handled at runtime
    websockets = None  # type: ignore[assignment]
    WebSocketClientProtocol = object  # type: ignore[misc]
    ConnectionClosed = ConnectionClosedError = ConnectionClosedOK = Exception  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class BestBidAsk:
    bid: float
    ask: float
    timestamp: datetime


DepthStreamData = OrderBookUpdate | OrderBookSnapshot


def _milliseconds_to_datetime(value: Any) -> datetime:
    try:
        millis = float(value)
    except (TypeError, ValueError):
        return datetime.now(tz=CURRENT_TIMEZONE)
    seconds = millis / 1000.0
    return datetime.fromtimestamp(seconds, tz=CURRENT_TIMEZONE)


def _build_level(price: float, quantity: float, timestamp: datetime) -> OrderBookLevel:
    notional = price * quantity
    return OrderBookLevel(
        price=price,
        quantity=quantity,
        notional=notional,
        first_seen_at=timestamp,
        last_update_at=timestamp,
        min_quantity_seen=quantity,
        max_quantity_seen=quantity,
    )


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

T = TypeVar("T")


class StreamSubscription(Generic[T]):
    """A simple subscription wrapper used by the application."""

    __slots__ = ("events", "_buffer", "_stop", "_stopped")

    def __init__(
        self,
        events: Iterator[StreamEvent[T]],
        buffer: StreamBuffer[T],
        stop_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        self.events = events
        self._buffer = buffer
        self._stop = stop_callback
        self._stopped = False

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._stop is not None:
            self._stop()


class StreamLimitError(RuntimeError):
    """Raised when stream scheduling exceeds the calculated limits."""


@dataclass
class _RateLimiter:
    """Simple window-based rate limiter used by the stream manager."""

    limit: int
    window: timedelta
    _events: Deque[datetime] = field(default_factory=deque, init=False, repr=False)

    def __post_init__(self) -> None:  # pragma: no cover - simple initializer
        # ensure window is at least positive to avoid division by zero semantics
        if self.window <= timedelta(0):
            object.__setattr__(self, "window", timedelta(seconds=1))

    def _trim(self, timestamp: datetime) -> None:
        while self._events and timestamp - self._events[0] >= self.window:
            self._events.popleft()

    def has_capacity(self, timestamp: datetime) -> bool:
        if self.limit <= 0:
            return True
        self._trim(timestamp)
        return len(self._events) < self.limit

    def commit(self, timestamp: datetime) -> None:
        if self.limit <= 0:
            return
        self._trim(timestamp)
        self._events.append(timestamp)


@dataclass(slots=True)
class _StreamMetrics:
    name: str
    latency_max_ms: float = 0.0
    latency_last_ms: float = 0.0
    delayed_messages: int = 0
    sequence_gaps: int = 0
    silence_timeouts: int = 0
    reconnects: int = 0
    queue_overflows: int = 0
    messages: int = 0

    def record_latency(self, latency_ms: float, threshold_ms: float) -> None:
        self.latency_last_ms = latency_ms
        if latency_ms > self.latency_max_ms:
            self.latency_max_ms = latency_ms
        if latency_ms > threshold_ms:
            self.delayed_messages += 1
        self.messages += 1

    def record_resync(self, reason: ResyncReason) -> None:
        if reason == ResyncReason.SEQUENCE_GAP:
            self.sequence_gaps += 1
        elif reason == ResyncReason.SILENCE_TIMEOUT:
            self.silence_timeouts += 1
        elif reason == ResyncReason.CONNECTION_LOST:
            self.reconnects += 1
        elif reason == ResyncReason.QUEUE_OVERFLOW:
            self.queue_overflows += 1


class _CommandBudget:
    __slots__ = ("_steady", "_burst", "_reserve_limit", "_reserve_used", "_lock")

    def __init__(self, limit: "StreamLimit") -> None:
        self._steady = _RateLimiter(limit.steady_per_min, timedelta(minutes=1))
        self._burst = _RateLimiter(limit.burst_per_5s, timedelta(seconds=5))
        self._reserve_limit = max(0, limit.resubscribe_buffer)
        self._reserve_used = 0
        self._lock = threading.Lock()

    def consume(self, *, priority: str = "normal") -> bool:
        """Consume from the configured budget.

        ``priority`` may be ``"resync"`` to use the reserve buffer.
        """

        use_reserve = priority != "normal"
        now = datetime.now(tz=timezone.utc)
        with self._lock:
            if use_reserve and self._reserve_used < self._reserve_limit:
                self._reserve_used += 1
                return True
            if not self._steady.has_capacity(now) or not self._burst.has_capacity(now):
                return False
            self._steady.commit(now)
            self._burst.commit(now)
            if self._reserve_used > 0:
                self._reserve_used -= 1
            return True


class _StreamValidationError(RuntimeError):
    __slots__ = ("reason", "details", "resubscribe")

    def __init__(
        self,
        reason: ResyncReason,
        details: str,
        *,
        resubscribe: bool = True,
    ) -> None:
        super().__init__(details)
        self.reason = reason
        self.details = details
        self.resubscribe = resubscribe


SnapshotFactory = Optional[Callable[[], Optional[StreamEvent[T]]]]


class _BinanceStreamWorker(Generic[T]):
    __slots__ = (
        "_stream",
        "_symbol",
        "_params",
        "_buffer",
        "_parser",
        "_metrics",
        "_silence_timeout_s",
        "_delay_threshold_ms",
        "_log",
        "_budget",
        "_snapshot_factory",
        "_stop_event",
        "_thread",
        "_command_queue",
        "_inflight",
        "_next_command_id",
        "_last_ping",
    )

    _BASE_ENDPOINT = "wss://fstream.binance.com/stream"

    def __init__(
        self,
        *,
        stream: str,
        symbol: str,
        params: Iterable[str],
        buffer: StreamBuffer[T],
        parser: Callable[[Dict[str, Any]], Iterable[StreamEvent[T]]],
        metrics: _StreamMetrics,
        silence_timeout_ms: int,
        log_writer: LogSink,
        budget: _CommandBudget,
        snapshot_factory: SnapshotFactory[T] = None,
    ) -> None:
        self._stream = stream
        self._symbol = symbol
        self._params = tuple(params)
        self._buffer = buffer
        self._parser = parser
        self._metrics = metrics
        self._silence_timeout_s = max(float(silence_timeout_ms) / 1000.0, 1.0)
        self._delay_threshold_ms = max(float(silence_timeout_ms) / 4.0, 250.0)
        self._log = log_writer
        self._budget = budget
        self._snapshot_factory = snapshot_factory
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"binance-{stream}-{symbol}",
            daemon=True,
        )
        self._command_queue: Deque[tuple[str, Tuple[str, ...], str]] = deque()
        self._inflight: Dict[int, tuple[str, Tuple[str, ...], str]] = {}
        self._next_command_id = 1
        self._last_ping = 0.0

    def start(self) -> None:
        if websockets is None:
            self._emit_resync(
                ResyncReason.CONNECTION_LOST,
                "websockets package is not available",
                enqueue_resubscribe=False,
            )
            return
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:  # pragma: no cover - background thread safety
            self._emit_resync(
                ResyncReason.CONNECTION_LOST,
                f"stream {self._stream} crashed: {exc}",
            )

    async def _main(self) -> None:
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                await self._connect_once()
                backoff = 1.0
            except Exception as exc:  # pragma: no cover - network safety
                if self._stop_event.is_set():
                    break
                details = f"{self._stream} connection lost: {exc}"
                self._emit_resync(ResyncReason.CONNECTION_LOST, details)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    async def _connect_once(self) -> None:
        self._log(f"connecting {self._stream} stream for {self._symbol}")
        try:
            async with websockets.connect(  # type: ignore[union-attr]
                self._BASE_ENDPOINT,
                ping_interval=None,
                close_timeout=5,
            ) as ws:
                await self._on_connected(ws)
                await self._recv_loop(ws)
        except ConnectionClosed as exc:
            if self._stop_event.is_set():
                return
            raise RuntimeError(f"connection closed: {exc}")

    async def _on_connected(self, ws: WebSocketClientProtocol) -> None:
        self._metrics.reconnects += 1
        self._queue_command("SUBSCRIBE", self._params, priority="resync")
        self._last_ping = time.monotonic()
        await self._flush_commands(ws)

    async def _recv_loop(self, ws: WebSocketClientProtocol) -> None:
        while not self._stop_event.is_set():
            await self._flush_commands(ws)
            await self._maybe_send_ping(ws)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=self._silence_timeout_s)
            except asyncio.TimeoutError:
                self._handle_timeout()
                self._queue_command("SUBSCRIBE", self._params, priority="resync")
                await self._flush_commands(ws)
                continue
            payload = self._decode_payload(raw)
            if payload is None:
                continue
            if "id" in payload and "result" in payload:
                self._handle_ack(payload)
                continue
            data = payload.get("data", payload)
            if not isinstance(data, dict):
                continue
            try:
                events = list(self._parser(data))
            except _StreamValidationError as exc:
                self._emit_resync(exc.reason, exc.details, enqueue_resubscribe=exc.resubscribe)
                self._push_snapshot()
                continue
            for event in events:
                self._record_latency(event.timestamp)
                self._push_event(event)

    async def _maybe_send_ping(self, ws: WebSocketClientProtocol) -> None:
        now = time.monotonic()
        interval = max(self._silence_timeout_s / 2.0, 10.0)
        if now - self._last_ping < interval:
            return
        try:
            await ws.ping()
            self._last_ping = now
        except Exception as exc:
            raise RuntimeError(f"ping failed: {exc}")

    def _decode_payload(self, raw: Any) -> Optional[Dict[str, Any]]:
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                return None
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if isinstance(payload, dict):
                return payload
            return None
        if isinstance(raw, dict):
            return raw
        return None

    def _queue_command(
        self,
        method: str,
        params: Iterable[str],
        *,
        priority: str = "normal",
    ) -> None:
        normalized_method = method.upper()
        normalized_params = tuple(params)
        for queued_method, queued_params, _ in self._command_queue:
            if queued_method == normalized_method and queued_params == normalized_params:
                return
        if normalized_method in {"SUBSCRIBE", "UNSUBSCRIBE"}:
            for inflight_method, inflight_params, _ in self._inflight.values():
                if inflight_method == normalized_method and inflight_params == normalized_params:
                    return
        command = (normalized_method, normalized_params, priority)
        if priority == "resync":
            self._command_queue.appendleft(command)
        else:
            self._command_queue.append(command)

    async def _flush_commands(self, ws: WebSocketClientProtocol) -> None:
        while self._command_queue and not self._stop_event.is_set():
            method, params, priority = self._command_queue[0]
            if not self._budget.consume(priority=priority):
                break
            self._command_queue.popleft()
            command_id = self._next_command_id
            self._next_command_id += 1
            command = {"id": command_id, "method": method, "params": list(params)}
            await ws.send(json.dumps(command))
            self._inflight[command_id] = (method, params, priority)

    def _handle_ack(self, payload: Dict[str, Any]) -> None:
        command_id = int(payload.get("id", -1))
        inflight = self._inflight.pop(command_id, None)
        now = datetime.now(tz=CURRENT_TIMEZONE)
        if inflight is None:
            return
        method, params, _priority = inflight
        error = payload.get("error")
        if error is not None:
            self._emit_resync(
                ResyncReason.CONNECTION_LOST,
                f"command error for {params}: {error}",
            )
        else:
            details = f"{method.lower()} confirmed for {' '.join(params)}"
            event = StreamEvent(StreamEventType.DATA, None, now, details=details)
            self._push_event(event)
        if method == "UNSUBSCRIBE" and not self._command_queue:
            self._stop_event.set()

    def _handle_timeout(self) -> None:
        details = f"{self._stream} silence timeout for {self._symbol}"
        self._emit_resync(
            ResyncReason.SILENCE_TIMEOUT,
            details,
            enqueue_resubscribe=True,
        )
        self._push_snapshot()

    def _record_latency(self, timestamp: datetime) -> None:
        now = datetime.now(tz=CURRENT_TIMEZONE)
        latency_ms = max((now - timestamp).total_seconds() * 1000.0, 0.0)
        self._metrics.record_latency(latency_ms, self._delay_threshold_ms)

    def _emit_resync(
        self,
        reason: ResyncReason,
        details: str,
        *,
        enqueue_resubscribe: bool = True,
    ) -> None:
        self._metrics.record_resync(reason)
        try:
            self._log(
                f"[{self._stream}:{self._symbol}] {details} ({reason.name})",
            )
        except Exception:  # pragma: no cover - logging failures ignored
            pass
        event = StreamEvent(
            StreamEventType.RESYNC,
            None,
            datetime.now(tz=CURRENT_TIMEZONE),
            reason=reason,
            details=details,
        )
        self._push_event(event)
        if enqueue_resubscribe:
            self._queue_command("SUBSCRIBE", self._params, priority="resync")

    def _push_event(self, event: StreamEvent[T]) -> None:
        appended = self._buffer.append(event)
        if appended:
            return
        self._metrics.record_resync(ResyncReason.QUEUE_OVERFLOW)
        overflow = StreamEvent(
            StreamEventType.RESYNC,
            None,
            datetime.now(tz=CURRENT_TIMEZONE),
            reason=ResyncReason.QUEUE_OVERFLOW,
            details=f"buffer overflow on {self._stream} stream",
        )
        self._buffer.append(overflow)

    def _push_snapshot(self) -> None:
        if self._snapshot_factory is None:
            return
        snapshot_event = self._snapshot_factory()
        if snapshot_event is None:
            return
        self._push_event(snapshot_event)


@dataclass(frozen=True, slots=True)
class BinanceSymbolStreams:
    exchange_data: "BinanceExchangeData"
    depth: "StreamSubscription[DepthStreamData]"
    trades: "StreamSubscription[Trade]"
    ticker: "StreamSubscription[BestBidAsk]"


class BinanceExchangeData:
    """A pragmatic placeholder implementation of the data interface."""

    PROFILE_WEIGHTS: Dict[str, float] = {
        TradingProfile.TOP.value: float(CONFIG.profile_weights.top),
        TradingProfile.LISTING.value: float(CONFIG.profile_weights.listing),
        TradingProfile.ALT.value: float(CONFIG.profile_weights.alt),
        TradingProfile.AUTO.value: float(CONFIG.profile_weights.auto),
    }

    def __init__(
        self,
        *,
        symbol: str,
        loop_interval_ms: int,
        silence_timeout_ms: int,
        log_writer: LogSink,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        exchange_info: Optional[Mapping[str, Mapping[str, object]]] = None,
    ) -> None:
        self._symbol = symbol.upper()
        self._loop_interval = max(loop_interval_ms, 100)
        self._silence_timeout = max(silence_timeout_ms, 1_000)
        self._log_writer = log_writer
        self._api_key = api_key
        self._api_secret = api_secret
        self._limits: StreamLimits = load_stream_limits()
        self._exchange_info: Dict[str, Dict[str, object]] = {}
        self._metrics: Dict[str, _StreamMetrics] = {
            "depth": _StreamMetrics("depth"),
            "trades": _StreamMetrics("trades"),
            "book_ticker": _StreamMetrics("book_ticker"),
        }
        self._command_budgets: Dict[str, _CommandBudget] = {
            "depth": _CommandBudget(self._limits.depth),
            "trades": _CommandBudget(self._limits.trades),
            "book_ticker": _CommandBudget(self._limits.book_ticker),
        }
        if exchange_info:
            self.update_exchange_info(exchange_info)
        if self._symbol not in self._exchange_info:
            self._load_exchange_info()

    # ------------------------------------------------------------------
    # Interface implementation expected by the application
    def fetch_symbol_filters(self) -> SymbolFilters:
        info = self._exchange_info.get(self._symbol, {})
        base_asset = self._symbol.replace("USDT", "")
        quote_asset = "USDT"
        price_tick = 0.1
        qty_step = 0.001
        min_notional = 5.0
        min_price = 0.0
        max_price = math.inf
        min_qty = qty_step
        max_qty = math.inf
        if isinstance(info, Mapping):
            base_asset = str(info.get("baseAsset", base_asset))
            quote_asset = str(info.get("quoteAsset", quote_asset))
            filters = info.get("filters")
            if isinstance(filters, Mapping):
                price_filter = filters.get("price")
                if isinstance(price_filter, Mapping):
                    price_tick = _safe_float(price_filter.get("tickSize"), price_tick)
                    min_price = _safe_float(price_filter.get("minPrice"), min_price)
                    max_price_value = _safe_float(price_filter.get("maxPrice"), max_price)
                    if max_price_value > 0.0:
                        max_price = max_price_value
                lot_filter = filters.get("lot")
                if isinstance(lot_filter, Mapping):
                    qty_step = _safe_float(lot_filter.get("stepSize"), qty_step)
                    min_qty_value = _safe_float(lot_filter.get("minQty"), min_qty)
                    min_qty = max(qty_step, min_qty_value)
                    max_qty_value = _safe_float(lot_filter.get("maxQty"), max_qty)
                    if max_qty_value > 0.0:
                        max_qty = max_qty_value
                notional_filter = filters.get("notional")
                if isinstance(notional_filter, Mapping):
                    min_notional = _safe_float(
                        notional_filter.get("minNotional"),
                        min_notional,
                    )
            price_tick = _safe_float(info.get("tickSize"), price_tick)
            qty_step = _safe_float(info.get("stepSize"), qty_step)
            min_notional = _safe_float(info.get("notional"), min_notional)
        return SymbolFilters(
            exchange=Exchange.BINANCE,
            symbol=self._symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price_tick_size=price_tick,
            quantity_step_size=qty_step,
            min_price=min_price,
            max_price=max_price,
            min_qty=min_qty,
            max_qty=max_qty,
            min_notional=min_notional,
        )

    def update_exchange_info(self, info: Mapping[str, Mapping[str, object]]) -> None:
        if not info:
            return
        updates: Dict[str, Dict[str, object]] = {}
        for symbol, entry in info.items():
            normalized = symbol.upper()
            if not normalized:
                continue
            if not isinstance(entry, Mapping):
                continue
            current = self._exchange_info.get(normalized)
            merged: Dict[str, object] = {}
            if isinstance(current, Mapping):
                merged.update(current)
            merged.update(dict(entry))
            updates[normalized] = merged
        if updates:
            self._exchange_info.update(updates)

    def set_symbol_profile(self, symbol: str, profile: TradingProfile) -> None:
        normalized = symbol.upper()
        entry = self._exchange_info.get(normalized)
        if isinstance(entry, Mapping):
            updated = dict(entry)
        else:
            updated = {}
        updated["profile"] = profile
        self._exchange_info[normalized] = updated

    def fetch_orderbook_snapshot(self) -> OrderBookSnapshot:
        now = datetime.now(tz=CURRENT_TIMEZONE)
        return OrderBookSnapshot(
            exchange=Exchange.BINANCE,
            symbol=self._symbol,
            last_update_id=0,
            bids=(),
            asks=(),
            received_at=now,
        )

    def fetch_next_funding_time(self) -> Optional[datetime]:
        return datetime.now(tz=CURRENT_TIMEZONE) + timedelta(hours=8)

    def stream_depth(self) -> StreamSubscription[DepthStreamData]:
        buffer: StreamBuffer[DepthStreamData] = StreamBuffer()
        metrics = self._metrics["depth"]
        budget = self._command_budgets["depth"]
        symbol_stream = f"{self._symbol.lower()}@depth@100ms"
        last_final_id: Optional[int] = None

        def snapshot_factory() -> Optional[StreamEvent[DepthStreamData]]:
            snapshot = self.fetch_orderbook_snapshot()
            return StreamEvent(StreamEventType.SNAPSHOT, snapshot, snapshot.received_at)

        def parser(message: Dict[str, Any]) -> Iterable[StreamEvent[DepthStreamData]]:
            nonlocal last_final_id
            event_type = str(message.get("e", "")).lower()
            if event_type != "depthupdate":
                return ()
            try:
                final_id = int(message.get("u"))
                prev_final = int(message.get("pu", final_id - 1))
                first_id = int(message.get("U", final_id))
            except (TypeError, ValueError):
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    "depth update ids missing",
                )
            if last_final_id is not None and prev_final != last_final_id:
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"depth sequence gap: expected {last_final_id}, got {prev_final}",
                )
            event_time = _milliseconds_to_datetime(message.get("E"))
            bids = self._parse_levels(message.get("b", ()), event_time, "bid")
            asks = self._parse_levels(message.get("a", ()), event_time, "ask")
            update = OrderBookUpdate(
                exchange=Exchange.BINANCE,
                symbol=self._symbol,
                first_update_id=first_id,
                last_update_id=final_id,
                bids=bids,
                asks=asks,
                event_time=event_time,
            )
            last_final_id = final_id
            return (StreamEvent(StreamEventType.DATA, update, event_time),)

        first_snapshot = snapshot_factory()
        if first_snapshot is not None:
            buffer.append(first_snapshot)

        worker = _BinanceStreamWorker[DepthStreamData](
            stream="depth",
            symbol=self._symbol,
            params=(symbol_stream,),
            buffer=buffer,
            parser=parser,
            metrics=metrics,
            silence_timeout_ms=self._silence_timeout,
            log_writer=self._log_writer,
            budget=budget,
            snapshot_factory=snapshot_factory,
        )
        worker.start()

        def generator() -> Iterator[StreamEvent[DepthStreamData]]:
            interval = self._loop_interval / 1000.0
            while True:
                pending = buffer.drain_pending()
                if pending:
                    for event in pending:
                        yield event
                    continue
                time.sleep(interval)

        return StreamSubscription(generator(), buffer, worker.stop)

    def stream_trades(self) -> StreamSubscription[Trade]:
        buffer: StreamBuffer[Trade] = StreamBuffer()
        metrics = self._metrics["trades"]
        budget = self._command_budgets["trades"]
        symbol_stream = f"{self._symbol.lower()}@aggTrade"
        last_trade_id: Optional[int] = None

        def parser(message: Dict[str, Any]) -> Iterable[StreamEvent[Trade]]:
            nonlocal last_trade_id
            event_type = str(message.get("e", "")).lower()
            if event_type != "aggtrade":
                return ()
            try:
                trade_id = int(message.get("a"))
                price = float(message.get("p", 0.0))
                quantity = float(message.get("q", 0.0))
            except (TypeError, ValueError):
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    "trade payload invalid",
                )
            if last_trade_id is not None and trade_id <= last_trade_id:
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"trade id regression: {trade_id} <= {last_trade_id}",
                )
            if quantity <= 0 or not math.isfinite(quantity):
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"trade quantity invalid: {quantity}",
                )
            if not math.isfinite(price) or price <= 0:
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"trade price invalid: {price}",
                )
            event_time = _milliseconds_to_datetime(message.get("T", message.get("E")))
            side = Side.ASK if bool(message.get("m")) else Side.BID
            trade = Trade(
                trade_id=str(trade_id),
                exchange=Exchange.BINANCE,
                symbol=self._symbol,
                executed_at=event_time,
                price=price,
                quantity=quantity,
                side=side,
            )
            last_trade_id = trade_id
            return (StreamEvent(StreamEventType.DATA, trade, event_time),)

        worker = _BinanceStreamWorker[Trade](
            stream="trades",
            symbol=self._symbol,
            params=(symbol_stream,),
            buffer=buffer,
            parser=parser,
            metrics=metrics,
            silence_timeout_ms=self._silence_timeout,
            log_writer=self._log_writer,
            budget=budget,
        )
        worker.start()

        def generator() -> Iterator[StreamEvent[Trade]]:
            interval = self._loop_interval / 1000.0
            while True:
                pending = buffer.drain_pending()
                if pending:
                    for event in pending:
                        yield event
                    continue
                time.sleep(interval)

        return StreamSubscription(generator(), buffer, worker.stop)

    def stream_book_ticker(self) -> StreamSubscription[BestBidAsk]:
        buffer: StreamBuffer[BestBidAsk] = StreamBuffer()
        metrics = self._metrics["book_ticker"]
        budget = self._command_budgets["book_ticker"]
        symbol_stream = f"{self._symbol.lower()}@bookTicker"
        last_update_id: Optional[int] = None

        def parser(message: Dict[str, Any]) -> Iterable[StreamEvent[BestBidAsk]]:
            nonlocal last_update_id
            event_type = str(message.get("e", "")).lower()
            if event_type != "bookticker":
                return ()
            try:
                update_id = int(message.get("u", 0))
                bid_price = float(message.get("b", 0.0))
                bid_qty = float(message.get("B", 0.0))
                ask_price = float(message.get("a", 0.0))
                ask_qty = float(message.get("A", 0.0))
            except (TypeError, ValueError):
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    "ticker payload invalid",
                    resubscribe=False,
                )
            if last_update_id is not None and update_id <= last_update_id:
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"ticker id regression: {update_id} <= {last_update_id}",
                    resubscribe=False,
                )
            if any(value < 0 for value in (bid_qty, ask_qty)):
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    "ticker volume negative",
                    resubscribe=False,
                )
            if bid_price < 0 or ask_price < 0 or not math.isfinite(bid_price) or not math.isfinite(ask_price):
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    "ticker price invalid",
                    resubscribe=False,
                )
            if bid_price > ask_price and ask_price > 0:
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"bid {bid_price} exceeds ask {ask_price}",
                    resubscribe=False,
                )
            event_time = _milliseconds_to_datetime(message.get("E", message.get("T")))
            best = BestBidAsk(bid=bid_price, ask=ask_price, timestamp=event_time)
            last_update_id = update_id
            return (StreamEvent(StreamEventType.DATA, best, event_time),)

        worker = _BinanceStreamWorker[BestBidAsk](
            stream="book_ticker",
            symbol=self._symbol,
            params=(symbol_stream,),
            buffer=buffer,
            parser=parser,
            metrics=metrics,
            silence_timeout_ms=self._silence_timeout,
            log_writer=self._log_writer,
            budget=budget,
        )
        worker.start()

        def generator() -> Iterator[StreamEvent[BestBidAsk]]:
            interval = self._loop_interval / 1000.0
            while True:
                pending = buffer.drain_pending()
                if pending:
                    for event in pending:
                        yield event
                    continue
                time.sleep(interval)

        return StreamSubscription(generator(), buffer, worker.stop)

    # ------------------------------------------------------------------
    def _parse_levels(
        self,
        entries: Iterable[Iterable[Any]],
        timestamp: datetime,
        side: str,
    ) -> Tuple[OrderBookLevel, ...]:
        levels: list[OrderBookLevel] = []
        for entry in entries:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            price_raw, quantity_raw = entry[0], entry[1]
            try:
                price = float(price_raw)
                quantity = float(quantity_raw)
            except (TypeError, ValueError):
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"{side} level malformed",
                )
            if not math.isfinite(price) or not math.isfinite(quantity):
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"{side} level non finite",
                )
            if quantity < 0:
                raise _StreamValidationError(
                    ResyncReason.SEQUENCE_GAP,
                    f"{side} level negative quantity",
                )
            levels.append(_build_level(price, quantity, timestamp))
        return tuple(levels)

    def _load_exchange_info(self) -> None:
        url = "https://api.binance.com/api/v3/exchangeInfo"
        try:
            with urlopen(url, timeout=5) as response:  # noqa: S310
                payload = json.load(response)
        except (URLError, TimeoutError, ValueError, OSError):
            return
        symbols: Sequence[Mapping[str, object]] = ()
        if isinstance(payload, Mapping):
            raw_symbols = payload.get("symbols")
            if isinstance(raw_symbols, Sequence) and not isinstance(raw_symbols, (str, bytes)):
                symbols = tuple(
                    entry
                    for entry in raw_symbols
                    if isinstance(entry, Mapping)
                )
        existing = self._exchange_info
        info: Dict[str, Dict[str, object]] = {}
        for entry in symbols:
            status = str(entry.get("status") or "").upper()
            if status != "TRADING":
                continue
            if not bool(entry.get("isSpotTradingAllowed", False)):
                continue
            permissions = entry.get("permissions")
            permissions_tuple: Tuple[str, ...] = ()
            if isinstance(permissions, Sequence) and not isinstance(permissions, (str, bytes)):
                normalized_permissions = {str(value).upper() for value in permissions}
                if "SPOT" not in normalized_permissions:
                    continue
                permissions_tuple = tuple(str(value) for value in permissions)
            symbol = str(entry.get("symbol") or "").upper()
            if not symbol:
                continue
            filter_entries: Sequence[Mapping[str, object]] = ()
            filters = entry.get("filters")
            if isinstance(filters, Sequence) and not isinstance(filters, (str, bytes)):
                filter_entries = tuple(
                    item for item in filters if isinstance(item, Mapping)
                )
            price_filter: Mapping[str, object] = next(
                (
                    item
                    for item in filter_entries
                    if str(item.get("filterType") or "").upper() == "PRICE_FILTER"
                ),
                {},
            )
            lot_filter: Mapping[str, object] = next(
                (
                    item
                    for item in filter_entries
                    if str(item.get("filterType") or "").upper() == "LOT_SIZE"
                ),
                {},
            )
            notional_filter: Mapping[str, object] = next(
                (
                    item
                    for item in filter_entries
                    if str(item.get("filterType") or "").upper() in {"MIN_NOTIONAL", "NOTIONAL"}
                ),
                {},
            )
            current = existing.get(symbol) if isinstance(existing, Mapping) else None
            previous_tick = 0.1
            previous_step = 0.001
            previous_notional = 5.0
            if isinstance(current, Mapping):
                previous_tick = _safe_float(current.get("tickSize"), previous_tick)
                previous_step = _safe_float(current.get("stepSize"), previous_step)
                previous_notional = _safe_float(current.get("notional"), previous_notional)
            min_price_value = max(_safe_float(price_filter.get("minPrice"), 0.0), 0.0)
            max_price_value = _safe_float(price_filter.get("maxPrice"), math.inf)
            if max_price_value <= 0.0:
                max_price_value = math.inf
            price_filters = {
                "minPrice": min_price_value,
                "maxPrice": max_price_value,
                "tickSize": max(_safe_float(price_filter.get("tickSize"), previous_tick), 10 ** -8),
            }
            step_size_value = max(_safe_float(lot_filter.get("stepSize"), previous_step), 10 ** -8)
            min_qty_value = max(_safe_float(lot_filter.get("minQty"), previous_step), step_size_value)
            max_qty_value = _safe_float(lot_filter.get("maxQty"), math.inf)
            if max_qty_value <= 0.0:
                max_qty_value = math.inf
            lot_filters = {
                "minQty": min_qty_value,
                "maxQty": max_qty_value,
                "stepSize": step_size_value,
            }
            min_notional_value = max(
                _safe_float(notional_filter.get("minNotional"), previous_notional),
                0.0,
            )
            notional_filters = {
                "minNotional": min_notional_value,
            }
            entry_info: Dict[str, object] = {
                "baseAsset": str(entry.get("baseAsset", "")),
                "quoteAsset": str(entry.get("quoteAsset", "")),
                "tickSize": price_filters["tickSize"],
                "stepSize": lot_filters["stepSize"],
                "notional": notional_filters["minNotional"],
                "filters": {
                    "price": price_filters,
                    "lot": lot_filters,
                    "notional": notional_filters,
                },
                "meta": {
                    "status": status,
                    "isSpotTradingAllowed": bool(entry.get("isSpotTradingAllowed", False)),
                    "onboardDate": entry.get("onboardDate"),
                    "permissions": permissions_tuple,
                    "baseAssetPrecision": entry.get("baseAssetPrecision"),
                    "quoteAssetPrecision": entry.get("quoteAssetPrecision"),
                    "quotePrecision": entry.get("quotePrecision"),
                },
            }
            if isinstance(current, Mapping) and "profile" in current:
                entry_info["profile"] = current["profile"]
            info[symbol] = entry_info
        if info:
            self._exchange_info = info


class BinanceStreamManager:
    """Manages Binance stream subscriptions with centralized limits."""

    def __init__(
        self,
        *,
        loop_interval_ms: int,
        silence_timeout_ms: int,
        log_writer: LogSink,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        self._loop_interval_ms = loop_interval_ms
        self._silence_timeout_ms = silence_timeout_ms
        self._log_writer = log_writer
        self._api_key = api_key
        self._api_secret = api_secret
        self._limits = load_stream_limits()
        self._exchange_data: Dict[str, BinanceExchangeData] = {}
        self._streams: Dict[str, BinanceSymbolStreams] = {}
        self._active: set[str] = set()
        self._profiles: Dict[str, TradingProfile] = {}
        self._weights: Dict[str, float] = {}
        self._max_symbols = self._resolve_capacity()
        self._reserve = self._resolve_reserve()
        self._limiters = self._build_limiters(self._limits)
        self._exchange_info_cache: Dict[str, Dict[str, object]] = {}

    @staticmethod
    def _build_limiters(limits: StreamLimits) -> Dict[str, tuple[_RateLimiter, _RateLimiter]]:
        return {
            "depth": (
                _RateLimiter(limits.depth.steady_per_min, timedelta(minutes=1)),
                _RateLimiter(limits.depth.burst_per_5s, timedelta(seconds=5)),
            ),
            "trades": (
                _RateLimiter(limits.trades.steady_per_min, timedelta(minutes=1)),
                _RateLimiter(limits.trades.burst_per_5s, timedelta(seconds=5)),
            ),
            "book_ticker": (
                _RateLimiter(limits.book_ticker.steady_per_min, timedelta(minutes=1)),
                _RateLimiter(limits.book_ticker.burst_per_5s, timedelta(seconds=5)),
            ),
        }

    def _resolve_capacity(self) -> int:
        values = [
            self._limits.depth.max_symbols,
            self._limits.trades.max_symbols,
            self._limits.book_ticker.max_symbols,
        ]
        positives = [value for value in values if value > 0]
        if not positives:
            return 0
        return min(positives)

    def _resolve_reserve(self) -> int:
        values = [
            self._limits.depth.resubscribe_buffer,
            self._limits.trades.resubscribe_buffer,
            self._limits.book_ticker.resubscribe_buffer,
        ]
        return max(values)

    def update_profiles(self, profiles: Mapping[str, TradingProfile]) -> None:
        for symbol, profile in profiles.items():
            normalized = symbol.upper()
            self._profiles[normalized] = profile
            exchange_data = self._exchange_data.get(normalized)
            if exchange_data is not None:
                exchange_data.set_symbol_profile(normalized, profile)

    def update_weights(self, weights: Mapping[str, float]) -> None:
        for symbol, weight in weights.items():
            self._weights[symbol.upper()] = float(weight)

    def update_exchange_info(self, info: Mapping[str, Mapping[str, object]]) -> None:
        if not info:
            return
        normalized: Dict[str, Dict[str, object]] = {}
        for symbol, entry in info.items():
            normalized_symbol = symbol.upper()
            if not normalized_symbol:
                continue
            if not isinstance(entry, Mapping):
                continue
            normalized[normalized_symbol] = dict(entry)
        if not normalized:
            return
        self._exchange_info_cache = normalized
        for exchange_data in self._exchange_data.values():
            exchange_data.update_exchange_info(normalized)

    def plan_subscriptions(self, symbols: Tuple[str, ...]) -> Tuple[str, ...]:
        if not symbols:
            return symbols
        available = self._available_slots()
        if available == 0:
            return tuple()
        ordered = sorted(
            symbols,
            key=lambda sym: self._profile_weight(sym),
            reverse=True,
        )
        planned: list[str] = []
        for symbol in ordered:
            if symbol in self._active:
                continue
            if available <= 0:
                break
            planned.append(symbol)
            available -= 1
        return tuple(planned)

    def _available_slots(self) -> int:
        if self._max_symbols <= 0:
            return 1_000_000
        active = len(self._active)
        reserve = min(self._reserve, self._max_symbols)
        capacity = self._max_symbols - reserve - active
        return max(capacity, 0)

    def _profile_weight(self, symbol: str) -> float:
        normalized = symbol.upper()
        weight = self._weights.get(normalized)
        if weight is not None:
            return weight
        profile = self._profiles.get(normalized, TradingProfile.AUTO)
        return float(BinanceExchangeData.PROFILE_WEIGHTS.get(profile.value, 0.0))

    def subscribe(self, symbol: str) -> BinanceSymbolStreams:
        symbol = symbol.upper()
        now = datetime.now(tz=timezone.utc)
        self._ensure_capacity(symbol)
        self._consume_limits(now)
        exchange_data = self._exchange_data.get(symbol)
        if exchange_data is None:
            exchange_data = BinanceExchangeData(
                symbol=symbol,
                loop_interval_ms=self._loop_interval_ms,
                silence_timeout_ms=self._silence_timeout_ms,
                log_writer=self._log_writer,
                api_key=self._api_key,
                api_secret=self._api_secret,
                exchange_info=self._exchange_info_cache,
            )
            self._exchange_data[symbol] = exchange_data
            profile = self._profiles.get(symbol)
            if profile is not None:
                exchange_data.set_symbol_profile(symbol, profile)
        streams = BinanceSymbolStreams(
            exchange_data=exchange_data,
            depth=exchange_data.stream_depth(),
            trades=exchange_data.stream_trades(),
            ticker=exchange_data.stream_book_ticker(),
        )
        self._streams[symbol] = streams
        self._active.add(symbol)
        return streams

    def get_exchange_data(self, symbol: str) -> BinanceExchangeData:
        symbol = symbol.upper()
        exchange_data = self._exchange_data.get(symbol)
        if exchange_data is None:
            exchange_data = BinanceExchangeData(
                symbol=symbol,
                loop_interval_ms=self._loop_interval_ms,
                silence_timeout_ms=self._silence_timeout_ms,
                log_writer=self._log_writer,
                api_key=self._api_key,
                api_secret=self._api_secret,
                exchange_info=self._exchange_info_cache,
            )
            self._exchange_data[symbol] = exchange_data
            profile = self._profiles.get(symbol)
            if profile is not None:
                exchange_data.set_symbol_profile(symbol, profile)
        return exchange_data

    def unsubscribe(self, symbol: str) -> None:
        symbol = symbol.upper()
        now = datetime.now(tz=timezone.utc)
        self._consume_limits(now)
        self._streams.pop(symbol, None)
        self._active.discard(symbol)

    def resubscribe(self, symbol: str) -> BinanceSymbolStreams:
        symbol = symbol.upper()
        now = datetime.now(tz=timezone.utc)
        self._consume_limits(now)
        exchange_data = self.get_exchange_data(symbol)
        streams = BinanceSymbolStreams(
            exchange_data=exchange_data,
            depth=exchange_data.stream_depth(),
            trades=exchange_data.stream_trades(),
            ticker=exchange_data.stream_book_ticker(),
        )
        self._streams[symbol] = streams
        self._active.add(symbol)
        return streams

    def cancel(self, symbol: str) -> None:
        """Release internal state without sending an unsubscribe command."""
        symbol = symbol.upper()
        self._streams.pop(symbol, None)
        self._active.discard(symbol)

    def _ensure_capacity(self, symbol: str) -> None:
        if self._max_symbols <= 0:
            return
        if symbol in self._active:
            return
        available = self._available_slots()
        if available <= 0:
            raise StreamLimitError("max stream capacity reached")

    def _consume_limits(self, timestamp: datetime) -> None:
        pending_limiters: list[_RateLimiter] = []
        for limiters in self._limiters.values():
            for limiter in limiters:
                if not limiter.has_capacity(timestamp):
                    raise StreamLimitError("stream command rate exceeded")
                pending_limiters.append(limiter)
        for limiter in pending_limiters:
            limiter.commit(timestamp)


