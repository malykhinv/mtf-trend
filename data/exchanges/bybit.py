from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)

from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config.config import CONFIG, STREAM_METRICS_LOG_INTERVAL_MIN
from config.timezone import CURRENT_TIMEZONE
from data.logger import LogSink
from domain.models import Exchange, OrderBookLevel, OrderBookSnapshot, OrderBookUpdate, Side, SymbolFilters, Trade

from .binance import BestBidAsk, DepthStreamData, StreamSubscription
from .events import ResyncReason, StreamEvent, StreamEventType
from .stream_buffer import StreamBuffer

try:  # pragma: no cover - optional dependency resolved at runtime
    import websockets
    from websockets import WebSocketClientProtocol
    from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK
except Exception:  # pragma: no cover - handled gracefully when websockets missing
    websockets = None  # type: ignore[assignment]
    WebSocketClientProtocol = object  # type: ignore[misc]
    ConnectionClosed = ConnectionClosedError = ConnectionClosedOK = Exception  # type: ignore[assignment]


T = TypeVar("T")


def _now() -> datetime:
    return datetime.now(tz=CURRENT_TIMEZONE)


def _to_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_datetime(value: Any) -> datetime:
    if value is None:
        return _now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).astimezone(CURRENT_TIMEZONE)
        return value.astimezone(CURRENT_TIMEZONE)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:
            seconds = seconds / 1000.0
        return datetime.fromtimestamp(seconds, tz=CURRENT_TIMEZONE)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return _to_datetime(int(text))
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return _now()
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(CURRENT_TIMEZONE)
    return _now()


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


class _BybitStreamError(RuntimeError):
    __slots__ = ("reason", "details")

    def __init__(self, reason: ResyncReason, details: str) -> None:
        super().__init__(details)
        self.reason = reason
        self.details = details


class _BybitRestClient:
    _REST_HOST = "https://api.bybit.com"

    def __init__(
        self,
        *,
        symbol: str,
        category: str,
        timeout_s: float,
        log: LogSink,
    ) -> None:
        self._symbol = symbol.upper()
        self._category = category
        self._timeout = max(timeout_s, 1.0)
        self._log = log

    def _request(
        self,
        path: str,
        params: Mapping[str, Any],
        *,
        context: str,
        max_attempts: int = 3,
    ) -> Mapping[str, Any]:
        query = dict(params)
        query.setdefault("category", self._category)
        query.setdefault("symbol", self._symbol)
        url = f"{self._REST_HOST}{path}?{urlencode(query)}"
        delay = 0.5
        last_exception: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                request = Request(url=url)
                with urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                    payload = response.read()
                data = json.loads(payload.decode("utf-8"))
                if not isinstance(data, Mapping):
                    raise RuntimeError("unexpected Bybit REST response type")
                return data
            except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_exception = exc
                if attempt < max_attempts:
                    try:
                        self._log(
                            (
                                f"[WARNING] Bybit REST {context} failed for {self._symbol} "
                                f"(attempt {attempt}): {exc}. Retrying in {delay:.2f}s"
                            )
                        )
                    except Exception:
                        pass
                    time.sleep(delay)
                    delay = min(delay * 2.0, 5.0)
                else:
                    break
        assert last_exception is not None
        raise RuntimeError(
            f"Bybit REST {context} failed for {self._symbol}"
        ) from last_exception

    def fetch_instrument(self) -> Mapping[str, Any]:
        response = self._request(
            "/v5/market/instruments-info",
            {},
            context="instrument info",
        )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("Bybit instrument info missing result")
        entries = result.get("list")
        if not isinstance(entries, Sequence) or not entries:
            raise RuntimeError("Bybit instrument list empty")
        instrument = entries[0]
        if not isinstance(instrument, Mapping):
            raise RuntimeError("Bybit instrument entry malformed")
        return instrument

    def fetch_orderbook(self, depth: int = 200) -> Mapping[str, Any]:
        params = {"limit": depth}
        response = self._request(
            "/v5/market/orderbook",
            params,
            context="orderbook",
        )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("Bybit orderbook result missing")
        return result

    def fetch_ticker(self) -> Mapping[str, Any]:
        response = self._request(
            "/v5/market/tickers",
            {},
            context="ticker",
        )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("Bybit ticker result missing")
        return result


SnapshotFactory = Callable[[], Iterable[StreamEvent[T]]]


@dataclass(slots=True)
class _BybitStreamWorker(Generic[T]):
    topic: str
    parser: Callable[[Mapping[str, Any]], Iterable[StreamEvent[T]]]
    buffer: StreamBuffer[T]
    log: LogSink
    silence_timeout_ms: int
    snapshot_factory: Optional[SnapshotFactory[T]] = None
    _report_interval: timedelta = field(init=False, repr=False)
    _last_report_at: datetime | None = field(init=False, repr=False, default=None)
    _messages_since_report: int = field(init=False, repr=False, default=0)
    _resyncs_since_report: int = field(init=False, repr=False, default=0)
    _exceptions_since_report: int = field(init=False, repr=False, default=0)

    _endpoint: str = "wss://stream.bybit.com/v5/public/linear"

    def __post_init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"bybit-{self.topic}",
            daemon=True,
        )
        self._report_interval = timedelta(minutes=STREAM_METRICS_LOG_INTERVAL_MIN)
        self._silence_timeout_s = max(float(self.silence_timeout_ms) / 1000.0, 5.0)

    def _register_messages(self, count: int) -> None:
        if count <= 0:
            return
        now = _now()
        self._messages_since_report += count
        if self._last_report_at is None:
            self._last_report_at = now
        self._log_summary_if_needed(now)

    def _register_resync(self) -> None:
        now = _now()
        self._resyncs_since_report += 1
        if self._last_report_at is None:
            self._last_report_at = now
        self._log_summary_if_needed(now)

    def _register_exception(self) -> None:
        now = _now()
        self._exceptions_since_report += 1
        if self._last_report_at is None:
            self._last_report_at = now
        self._log_summary_if_needed(now)

    def _log_summary_if_needed(self, now: datetime) -> None:
        if self._last_report_at is None:
            self._last_report_at = now
            return
        if now - self._last_report_at < self._report_interval:
            return
        report = (
            f"[{self.topic}] Сводка за {STREAM_METRICS_LOG_INTERVAL_MIN}м: "
            f"сообщений={self._messages_since_report}, "
            f"ресинки={self._resyncs_since_report}, "
            f"исключения={self._exceptions_since_report}"
        )
        try:
            self.log(report)
        except Exception:
            pass
        self._last_report_at = now
        self._messages_since_report = 0
        self._resyncs_since_report = 0
        self._exceptions_since_report = 0

    def start(self) -> None:
        if websockets is None:
            try:
                self.log(
                    f"[ERROR] websockets library unavailable; cannot start Bybit stream {self.topic}"
                )
            except Exception:
                pass
            self._emit_resync(ResyncReason.CONNECTION_LOST, "websockets unavailable")
            return
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._consume())
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    async def _consume(self) -> None:
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                assert websockets is not None
                async with websockets.connect(  # type: ignore[attr-defined]
                    self._endpoint,
                    ping_interval=None,
                    close_timeout=5.0,
                ) as ws:
                    await self._subscribe(ws)
                    self._push_snapshot()
                    backoff = 1.0
                    while not self._stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(),
                                timeout=self._silence_timeout_s,
                            )
                        except asyncio.TimeoutError:
                            self._emit_resync(
                                ResyncReason.SILENCE_TIMEOUT,
                                f"{self.topic} silence timeout",
                            )
                            self._push_snapshot()
                            break
                        if raw is None:
                            continue
                        payload = self._parse_message(raw, ws)
                        if payload is None:
                            continue
                        try:
                            events = list(self.parser(payload))
                        except _BybitStreamError as exc:
                            self._register_exception()
                            self._emit_resync(exc.reason, exc.details)
                            self._push_snapshot()
                            break
                        if not events:
                            continue
                        self._register_messages(len(events))
                        for event in events:
                            appended = self.buffer.append(event)
                            if not appended:
                                self._register_resync()
                                overflow = StreamEvent(
                                    StreamEventType.RESYNC,
                                    None,
                                    _now(),
                                    reason=ResyncReason.QUEUE_OVERFLOW,
                                    details=f"buffer overflow on {self.topic}",
                                )
                                self.buffer.append(overflow)
                        continue
            except (ConnectionClosedOK,):
                break
            except (ConnectionClosed, ConnectionClosedError, OSError, asyncio.TimeoutError) as exc:
                self._register_exception()
                self._emit_resync(
                    ResyncReason.CONNECTION_LOST,
                    f"{self.topic} connection lost: {exc}",
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                self._register_exception()
                self._emit_resync(
                    ResyncReason.CONNECTION_LOST,
                    f"{self.topic} stream error: {exc}",
                )
            if self._stop_event.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)

    async def _subscribe(self, ws: WebSocketClientProtocol) -> None:
        subscribe = json.dumps({"op": "subscribe", "args": [self.topic]})
        await ws.send(subscribe)
        ack_deadline = time.monotonic() + 5.0
        while time.monotonic() < ack_deadline and not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            payload = self._parse_message(raw, ws)
            if payload is None:
                continue
            if isinstance(payload, Mapping) and payload.get("op") == "subscribe":
                success = bool(payload.get("success", False))
                if success:
                    try:
                        self.log(f"[INFO] Bybit subscribe ok for {self.topic}")
                    except Exception:
                        pass
                    return
                message = str(payload.get("ret_msg") or "subscribe failed")
                raise RuntimeError(message)
            if payload.get("topic") == self.topic:
                # Received data before explicit ack; treat as success.
                return
        raise RuntimeError("subscribe ack timeout")

    def _parse_message(
        self,
        raw: Any,
        ws: WebSocketClientProtocol,
    ) -> Optional[Mapping[str, Any]]:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="ignore")
        else:
            text = str(raw)
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, MutableMapping):
            return None
        op = payload.get("op")
        if op == "ping":
            pong = json.dumps({"op": "pong"})
            try:
                asyncio.ensure_future(ws.send(pong))
            except Exception:
                pass
            return None
        if op == "pong":
            return None
        if op == "subscribe":
            return payload
        return payload

    def _emit_resync(self, reason: ResyncReason, details: str) -> None:
        self._register_resync()
        event = StreamEvent(
            StreamEventType.RESYNC,
            None,
            _now(),
            reason=reason,
            details=details,
        )
        self.buffer.append(event)
        try:
            self.log(f"[INFO] Bybit stream {self.topic} resync: {details} ({reason.name})")
        except Exception:  # pragma: no cover - logging failures ignored
            pass

    def _push_snapshot(self) -> None:
        if self.snapshot_factory is None:
            return
        try:
            events = tuple(self.snapshot_factory())
        except Exception as exc:
            self._register_exception()
            self._emit_resync(
                ResyncReason.CONNECTION_LOST,
                f"failed to refresh snapshot for {self.topic}: {exc}",
            )
            return
        if not events:
            return
        self.buffer.extend(events)


class BybitExchangeData:
    _REST_DEPTH = 200

    def __init__(
        self,
        *,
        symbol: str,
        loop_interval_ms: int,
        silence_timeout_ms: int,
        log_writer: LogSink,
        category: str = "linear",
    ) -> None:
        self._symbol = symbol.upper()
        timeout = getattr(CONFIG.general, "orderbook_snapshot_timeout_s", 5.0)
        self._rest = _BybitRestClient(
            symbol=self._symbol,
            category=category,
            timeout_s=timeout,
            log=log_writer,
        )
        self._loop_interval = max(loop_interval_ms, 100)
        self._silence_timeout = max(silence_timeout_ms, 5_000)
        self._log = log_writer
        self._category = category
        self._instrument_cache: Optional[Mapping[str, Any]] = None

    # ------------------------------------------------------------------
    def fetch_symbol_filters(self) -> SymbolFilters:
        info = self._instrument()
        base_asset = str(info.get("baseCoin", self._symbol.replace("USDT", "")))
        quote_asset = str(info.get("quoteCoin", "USDT"))
        price_filter = info.get("priceFilter")
        if isinstance(price_filter, Mapping):
            price_tick = _to_float(price_filter.get("tickSize"), default=0.0)
            min_price = _to_float(price_filter.get("minPrice"), default=0.0)
            max_price = _to_float(price_filter.get("maxPrice"), default=math.inf)
        else:
            price_tick = 0.0
            min_price = 0.0
            max_price = math.inf
        lot_filter = info.get("lotSizeFilter")
        if isinstance(lot_filter, Mapping):
            qty_step = _to_float(lot_filter.get("qtyStep"), default=0.0)
            min_qty = _to_float(
                lot_filter.get("minOrderQty"),
                default=qty_step if qty_step > 0.0 else 0.0,
            )
            max_qty = _to_float(lot_filter.get("maxOrderQty"), default=math.inf)
            min_notional = _to_float(lot_filter.get("minOrderAmt"), default=0.0)
        else:
            qty_step = 0.0
            min_qty = 0.0
            max_qty = math.inf
            min_notional = 0.0
        return SymbolFilters(
            exchange=Exchange.BYBIT,
            symbol=self._symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price_tick_size=price_tick if price_tick > 0.0 else 0.0001,
            quantity_step_size=qty_step if qty_step > 0.0 else 0.001,
            min_price=min_price,
            max_price=max_price,
            min_qty=min_qty,
            max_qty=max_qty,
            min_notional=min_notional,
        )

    def fetch_orderbook_snapshot(self) -> OrderBookSnapshot:
        payload = self._rest.fetch_orderbook(depth=self._REST_DEPTH)
        bids_raw = payload.get("b") or payload.get("bids")
        asks_raw = payload.get("a") or payload.get("asks")
        timestamp = _to_datetime(payload.get("ts"))
        sequence = _to_int(payload.get("u")) or _to_int(payload.get("seq")) or 0

        def build(levels: Any, side: str) -> Tuple[OrderBookLevel, ...]:
            result: list[OrderBookLevel] = []
            if not isinstance(levels, Sequence):
                return tuple()
            for entry in levels:
                if not isinstance(entry, Sequence) or len(entry) < 2:
                    continue
                price = _to_float(entry[0], default=0.0)
                quantity = _to_float(entry[1], default=0.0)
                if not math.isfinite(price) or price <= 0.0:
                    continue
                if not math.isfinite(quantity) or quantity < 0.0:
                    continue
                result.append(_build_level(price, quantity, timestamp))
            return tuple(result)

        bids = build(bids_raw, "bid")
        asks = build(asks_raw, "ask")
        return OrderBookSnapshot(
            exchange=Exchange.BYBIT,
            symbol=self._symbol,
            last_update_id=sequence,
            bids=bids,
            asks=asks,
            received_at=timestamp,
        )

    def fetch_next_funding_time(self) -> Optional[datetime]:
        info = self._instrument()
        next_time = info.get("nextFundingTime") or info.get("nextFundingAt")
        if next_time:
            return _to_datetime(next_time)
        ticker = self._rest.fetch_ticker()
        data = ticker.get("list")
        if isinstance(data, Sequence) and data:
            entry = data[0]
            if isinstance(entry, Mapping):
                candidate = entry.get("nextFundingTime") or entry.get("fundingTime")
                if candidate:
                    return _to_datetime(candidate)
        return None

    def stream_depth(self) -> StreamSubscription[DepthStreamData]:
        buffer: StreamBuffer[DepthStreamData] = StreamBuffer()
        state: Dict[str, Optional[int]] = {"last_seq": None}

        def snapshot_factory() -> Iterable[StreamEvent[DepthStreamData]]:
            snapshot = self.fetch_orderbook_snapshot()
            state["last_seq"] = snapshot.last_update_id
            details = f"seq={snapshot.last_update_id}"
            return (
                StreamEvent(
                    StreamEventType.SNAPSHOT,
                    snapshot,
                    snapshot.received_at,
                    details=details,
                ),
            )

        def parser(message: Mapping[str, Any]) -> Iterable[StreamEvent[DepthStreamData]]:
            topic = str(message.get("topic", ""))
            if not topic.startswith("orderbook"):
                return ()
            data = message.get("data")
            if not isinstance(data, Sequence) or not data:
                return ()
            entry = data[0]
            if not isinstance(entry, Mapping):
                return ()
            msg_type = str(message.get("type", "delta")).lower()
            seq = _to_int(entry.get("seq")) or _to_int(entry.get("u"))
            prev_seq = (
                _to_int(entry.get("prevSeq"))
                or _to_int(entry.get("pu"))
                or (_to_int(entry.get("seq")) - 1 if _to_int(entry.get("seq")) else None)
            )
            timestamp = _to_datetime(message.get("ts") or entry.get("ts"))

            def convert(levels: Any) -> Tuple[OrderBookLevel, ...]:
                book: list[OrderBookLevel] = []
                if not isinstance(levels, Sequence):
                    return tuple()
                for level in levels:
                    if not isinstance(level, Sequence) or len(level) < 2:
                        continue
                    price = _to_float(level[0], default=0.0)
                    quantity = _to_float(level[1], default=0.0)
                    if not math.isfinite(price) or price <= 0.0:
                        continue
                    if not math.isfinite(quantity) or quantity < 0.0:
                        continue
                    book.append(_build_level(price, quantity, timestamp))
                return tuple(book)

            if msg_type == "snapshot":
                snapshot = OrderBookSnapshot(
                    exchange=Exchange.BYBIT,
                    symbol=self._symbol,
                    last_update_id=seq or 0,
                    bids=convert(entry.get("b")),
                    asks=convert(entry.get("a")),
                    received_at=timestamp,
                )
                state["last_seq"] = snapshot.last_update_id
                details = f"seq={snapshot.last_update_id}"
                return (
                    StreamEvent(
                        StreamEventType.SNAPSHOT,
                        snapshot,
                        snapshot.received_at,
                        details=details,
                    ),
                )

            last_seq = state.get("last_seq")
            if last_seq is not None and prev_seq is not None and prev_seq != last_seq:
                raise _BybitStreamError(
                    ResyncReason.SEQUENCE_GAP,
                    f"depth sequence gap: prev {prev_seq} last {last_seq}",
                )
            update_id = seq if seq is not None else (last_seq if last_seq is not None else 0)
            update = OrderBookUpdate(
                exchange=Exchange.BYBIT,
                symbol=self._symbol,
                first_update_id=update_id,
                last_update_id=update_id,
                bids=convert(entry.get("b")),
                asks=convert(entry.get("a")),
                event_time=timestamp,
            )
            state["last_seq"] = update.last_update_id
            return (StreamEvent(StreamEventType.DATA, update, timestamp),)

        worker = _BybitStreamWorker[
            DepthStreamData
        ](
            topic=f"orderbook.50.{self._symbol}",
            parser=parser,
            buffer=buffer,
            log=self._log,
            silence_timeout_ms=self._silence_timeout,
            snapshot_factory=snapshot_factory,
        )
        buffer.extend(snapshot_factory())
        worker.start()
        subscription = StreamSubscription(
            self._stream_iterator(buffer),
            buffer,
            stop_callback=worker.stop,
        )
        return subscription

    def stream_trades(self) -> StreamSubscription[Trade]:
        buffer: StreamBuffer[Trade] = StreamBuffer()

        def parser(message: Mapping[str, Any]) -> Iterable[StreamEvent[Trade]]:
            topic = str(message.get("topic", ""))
            if not topic.startswith("publicTrade"):
                return ()
            data = message.get("data")
            if not isinstance(data, Sequence):
                return ()
            events: list[StreamEvent[Trade]] = []
            for entry in data:
                if not isinstance(entry, Mapping):
                    continue
                trade_id = str(entry.get("i"))
                price = _to_float(entry.get("p"), default=0.0)
                quantity = _to_float(entry.get("v"), default=0.0)
                timestamp = _to_datetime(entry.get("T") or message.get("ts"))
                side_text = str(entry.get("S") or entry.get("L") or "").lower()
                side = Side.BID if side_text in ("buy", "b", "bid") else Side.ASK
                trade = Trade(
                    trade_id=trade_id,
                    exchange=Exchange.BYBIT,
                    symbol=self._symbol,
                    executed_at=timestamp,
                    price=price,
                    quantity=quantity,
                    side=side,
                )
                events.append(StreamEvent(StreamEventType.DATA, trade, timestamp))
            return tuple(events)

        worker = _BybitStreamWorker[Trade](
            topic=f"publicTrade.{self._symbol}",
            parser=parser,
            buffer=buffer,
            log=self._log,
            silence_timeout_ms=self._silence_timeout,
        )
        worker.start()
        subscription = StreamSubscription(
            self._stream_iterator(buffer),
            buffer,
            stop_callback=worker.stop,
        )
        return subscription

    def stream_book_ticker(self) -> StreamSubscription[BestBidAsk]:
        buffer: StreamBuffer[BestBidAsk] = StreamBuffer()

        def parser(message: Mapping[str, Any]) -> Iterable[StreamEvent[BestBidAsk]]:
            topic = str(message.get("topic", ""))
            if not topic.startswith("tickers"):
                return ()
            data = message.get("data")
            if not isinstance(data, Sequence) or not data:
                return ()
            entry = data[0]
            if not isinstance(entry, Mapping):
                return ()
            bid = _to_float(entry.get("bid1Price"), default=0.0)
            ask = _to_float(entry.get("ask1Price"), default=0.0)
            event_time = _to_datetime(message.get("ts") or entry.get("ts"))
            best = BestBidAsk(bid_price=bid, ask_price=ask, event_time=event_time)
            return (StreamEvent(StreamEventType.DATA, best, event_time),)

        worker = _BybitStreamWorker[BestBidAsk](
            topic=f"tickers.{self._symbol}",
            parser=parser,
            buffer=buffer,
            log=self._log,
            silence_timeout_ms=self._silence_timeout,
        )
        worker.start()
        subscription = StreamSubscription(
            self._stream_iterator(buffer),
            buffer,
            stop_callback=worker.stop,
        )
        return subscription

    # ------------------------------------------------------------------
    def _instrument(self) -> Mapping[str, Any]:
        if self._instrument_cache is None:
            self._instrument_cache = self._rest.fetch_instrument()
        return self._instrument_cache

    def update_exchange_info(self, info: Mapping[str, Mapping[str, object]]) -> None:
        if not info:
            return
        normalized = info.get(self._symbol)
        if isinstance(normalized, Mapping):
            self._instrument_cache = normalized

    def _stream_iterator(
        self,
        buffer: StreamBuffer[T],
    ) -> Iterator[StreamEvent[T]]:
        interval = max(self._loop_interval / 1000.0, 0.1)
        while True:
            pending = buffer.drain_pending()
            if pending:
                for event in pending:
                    yield event
                continue
            time.sleep(interval)


__all__ = ["BybitExchangeData"]

