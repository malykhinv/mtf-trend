from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.client import RemoteDisconnected
from typing import Any, Callable, ClassVar, Iterable, Iterator, Optional
from urllib import parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from domain.models import (
    Candle,
    Exchange,
    OrderBookLevel,
    OrderBookSnapshot,
    OrderBookUpdate,
    Side,
    SymbolFilters,
    Trade,
)
from utils.async_websocket import ThreadedWebSocketClient, WebSocketTimeoutError
from utils.timez import from_exchange_timestamp, get_current_time

from .base import (
    BestBidAsk,
    DepthStreamData,
    ExchangeLogger,
    ResyncReason,
    StreamBuffer,
    StreamEvent,
    StreamSubscription,
)
from .binance_stream_pool import BinanceStreamPool


WebSocketClient = ThreadedWebSocketClient


@dataclass(slots=True)
class BinanceEndpoints:
    rest_base: str = "https://fapi.binance.com"
    ws_base: str = "wss://fstream.binance.com/ws"

BINANCE_ALLOWED_DEPTH_LIMITS: frozenset[int] = frozenset({5, 10, 20, 50, 100, 500, 1000})
MIN_STREAM_SILENCE_TIMEOUT_MS = 1500.0


class BinanceExchangeData:
    _pool_lock: ClassVar[threading.Lock] = threading.Lock()
    _shared_stream_pool: ClassVar[BinanceStreamPool | None] = None

    def __init__(
        self,
        symbol: str,
        loop_interval_ms: int = 100,
        depth_limit: int = 500,
        rest_timeout: float = 5.0,
        rest_retries: int = 3,
        rest_retry_delay: float = 0.5,
        rest_retry_backoff: float = 2.0,
        ws_timeout: float = 10.0,
        reconnect_delay: float = 1.0,
        log_writer: Optional[Callable[[str], None]] = None,
        endpoints: Optional[BinanceEndpoints] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        silence_timeout_ms: float | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self._endpoints = endpoints or BinanceEndpoints()
        self._rest_timeout = rest_timeout
        self._rest_retries = max(0, int(rest_retries))
        self._rest_retry_delay = max(0.0, float(rest_retry_delay))
        self._rest_retry_backoff = max(1.0, float(rest_retry_backoff))
        self._ws_timeout = ws_timeout
        self._reconnect_delay = reconnect_delay
        self._logger = ExchangeLogger(f"Binance:{self.symbol}", log_writer)
        self._stream_pool = self._get_stream_pool(
            endpoints=self._endpoints,
            ws_timeout=self._ws_timeout,
            reconnect_delay=self._reconnect_delay,
            log_writer=log_writer,
        )
        self._depth_limit = self._normalize_depth_limit(depth_limit)
        if silence_timeout_ms is not None:
            effective_silence_timeout_ms = max(
                float(silence_timeout_ms),
                MIN_STREAM_SILENCE_TIMEOUT_MS,
            )
        else:
            effective_silence_timeout_ms = MIN_STREAM_SILENCE_TIMEOUT_MS
        self._silence_timeout = max(0.1, effective_silence_timeout_ms / 1000.0)
        self._heartbeat_interval = max(self._silence_timeout / 2, 0.1)
        self._depth_last_update: Optional[int] = None
        self._depth_buffered_messages: list[dict[str, Any]] = []
        self._depth_allow_skip = False
        self._lock = threading.Lock()
        self._api_key = api_key
        self._api_secret = api_secret

    def _normalize_depth_limit(self, depth_limit: int) -> int:
        default_limit = 500
        allowed = sorted(BINANCE_ALLOWED_DEPTH_LIMITS)
        try:
            requested_limit = int(depth_limit)
        except (TypeError, ValueError):
            self._logger.log(
                f"Binance depth limit {depth_limit!r} is invalid, using {default_limit} instead"
            )
            return default_limit

        if requested_limit <= 0:
            self._logger.log(
                f"Binance depth limit {requested_limit} is unsupported, using {default_limit} instead"
            )
            return default_limit

        if requested_limit in BINANCE_ALLOWED_DEPTH_LIMITS:
            return requested_limit

        normalized = min(allowed, key=lambda value: abs(value - requested_limit))
        self._logger.log(
            f"Binance depth limit {requested_limit} is unsupported, using {normalized} instead"
        )
        return normalized

    @classmethod
    def _get_stream_pool(
        cls,
        *,
        endpoints: BinanceEndpoints,
        ws_timeout: float,
        reconnect_delay: float,
        log_writer: Optional[Callable[[str], None]],
    ) -> BinanceStreamPool:
        with cls._pool_lock:
            if cls._shared_stream_pool is None:
                cls._shared_stream_pool = BinanceStreamPool(
                    endpoints_ws_base=endpoints.ws_base,
                    ws_timeout=ws_timeout,
                    reconnect_delay=reconnect_delay,
                    log_writer=log_writer,
                )
            return cls._shared_stream_pool

    def _rest_get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        params = params or {}
        query = parse.urlencode(params)
        url = f"{self._endpoints.rest_base}{path}"
        if query:
            url = f"{url}?{query}"
        req = Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        return self._execute_rest_request(req)

    def _execute_rest_request(self, request: Request) -> Any:
        retries_remaining = self._rest_retries
        delay = self._rest_retry_delay
        while True:
            try:
                with urlopen(request, timeout=self._rest_timeout) as resp:
                    payload = resp.read().decode("utf-8")
                return json.loads(payload)
            except HTTPError:
                raise
            except (
                URLError,
                RemoteDisconnected,
                TimeoutError,
                socket.timeout,
                ConnectionError,
            ):
                if retries_remaining <= 0:
                    raise
                if delay > 0.0:
                    time.sleep(delay)
                retries_remaining -= 1
                delay *= self._rest_retry_backoff

    def fetch_symbol_filters(self) -> SymbolFilters:
        data = self._rest_get("/fapi/v1/exchangeInfo", {"symbol": self.symbol})
        symbols = data.get("symbols") or []
        if not symbols:
            raise ValueError(f"Symbol {self.symbol} not found in exchangeInfo response")
        info = symbols[0]
        filters = {item["filterType"]: item for item in info.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_filter = filters.get("LOT_SIZE", {})
        notional_filter = filters.get("MIN_NOTIONAL", {})
        return SymbolFilters(
            exchange=Exchange.BINANCE,
            symbol=self.symbol,
            base_asset=info.get("baseAsset", ""),
            quote_asset=info.get("quoteAsset", ""),
            price_tick_size=float(price_filter.get("tickSize", "0.0")),
            quantity_step_size=float(lot_filter.get("stepSize", "0.0")),
            min_price=float(price_filter.get("minPrice", "0.0")),
            max_price=float(price_filter.get("maxPrice", "0.0")),
            min_qty=float(lot_filter.get("minQty", "0.0")),
            max_qty=float(lot_filter.get("maxQty", "0.0")),
            min_notional=float(notional_filter.get("notional", "0.0")),
        )

    def fetch_orderbook_snapshot(self) -> OrderBookSnapshot:
        data = self._rest_get(
            "/fapi/v1/depth",
            {"symbol": self.symbol, "limit": self._depth_limit},
        )
        last_update_id = int(data["lastUpdateId"])
        now = get_current_time()
        bids = tuple(
            self._build_level(float(price), float(qty), now)
            for price, qty in data.get("bids", [])
        )
        asks = tuple(
            self._build_level(float(price), float(qty), now)
            for price, qty in data.get("asks", [])
        )
        snapshot = OrderBookSnapshot(
            exchange=Exchange.BINANCE,
            symbol=self.symbol,
            last_update_id=last_update_id,
            bids=bids,
            asks=asks,
            received_at=now,
        )
        return snapshot

    def fetch_next_funding_time(self) -> Optional[datetime]:
        response = self._rest_get(
            "/fapi/v1/fundingRate",
            {"symbol": self.symbol, "limit": 1},
        )
        if not response:
            return None
        entry = response[0]
        funding_time_raw = entry.get("fundingTime")
        if funding_time_raw is None:
            return None
        last_funding = from_exchange_timestamp(funding_time_raw)
        interval = timedelta(hours=8)
        if interval <= timedelta(0):
            return None
        next_funding = last_funding + interval
        now = get_current_time()
        while next_funding <= now:
            next_funding += interval
        return next_funding

    def stream_depth(self) -> StreamSubscription[DepthStreamData]:
        silence_timeout = self._silence_timeout
        heartbeat_interval = self._heartbeat_interval

        buffer: StreamBuffer[DepthStreamData] = StreamBuffer(
            name="depth",
            logger=self._logger,
            silence_timeout=silence_timeout,
            heartbeat_interval=heartbeat_interval,
        )
        self._reset_depth_state()

        def handle_message(message: dict[str, Any]) -> None:
            self._handle_depth_message(message, buffer)

        def handle_error(reason: ResyncReason, details: str) -> None:
            suffix = f"(symbol {self.symbol})"
            message = details if suffix in details else f"{details} {suffix}"
            should_log = not (
                reason == ResyncReason.CONNECTION_LOST
                and "ошибка чтения сокета" in details
            )
            buffer.push_resync(reason, message)
            if should_log:
                self._logger.log(message)

        release = self._stream_pool.register_depth(
            self.symbol,
            buffer,
            handle_message,
            handle_error,
        )

        self._start_depth_snapshot(buffer)

        def iterator() -> Iterator[StreamEvent[DepthStreamData]]:
            try:
                while True:
                    yield buffer.next()
            finally:
                buffer.stop()
                release()

        return StreamSubscription(events=iterator(), _buffer=buffer, _stopper=release)

    def _start_depth_snapshot(self, buffer: StreamBuffer[DepthStreamData]) -> None:
        def load_snapshot() -> None:
            while not buffer.stopped():
                try:
                    snapshot = self.fetch_orderbook_snapshot()
                except Exception as exc:  # noqa: BLE001
                    details = (
                        "Binance depth stream: не удалось получить начальный снапшот: "
                        f"{exc}"
                    )
                    buffer.push_resync(ResyncReason.CONNECTION_LOST, details)
                    self._logger.log_resync(ResyncReason.CONNECTION_LOST, details)
                    self._reset_depth_state()
                    time.sleep(self._reconnect_delay)
                    continue
                self._apply_depth_snapshot(snapshot, buffer)
                break

        threading.Thread(
            target=load_snapshot,
            name=f"binance-depth-snapshot-{self.symbol.lower()}",
            daemon=True,
        ).start()

    def _handle_depth_message(
        self,
        message: dict[str, Any],
        buffer: StreamBuffer[DepthStreamData],
        *,
        buffer_if_uninitialized: bool = True,
        allow_skip: bool = False,
    ) -> None:
        if message.get("e") != "depthUpdate":
            return
        first_update = int(message.get("U", 0))
        last_update = int(message.get("u", 0))
        prev_update = int(message.get("pu", first_update - 1))
        event_time = from_exchange_timestamp(message.get("E", 0) / 1000.0)

        update: Optional[OrderBookUpdate] = None
        resync_reason: Optional[ResyncReason] = None
        resync_details = ""

        with self._lock:
            if self._depth_last_update is None:
                if buffer_if_uninitialized and last_update != 0:
                    self._depth_buffered_messages.append(message)
                return

            expected = self._depth_last_update + 1
            allow_skip_current = allow_skip or self._depth_allow_skip

            if last_update <= self._depth_last_update:
                return

            if first_update > expected:
                if allow_skip_current:
                    return
                resync_reason = ResyncReason.SEQUENCE_GAP
                resync_details = (
                    f"Ожидали {expected}, получили диапазон {first_update}-{last_update}"
                )
            elif prev_update != self._depth_last_update:
                if allow_skip_current:
                    return
                resync_reason = ResyncReason.SEQUENCE_GAP
                resync_details = (
                    f"Предыдущий апдейт {prev_update} != {self._depth_last_update}"
                )
            else:
                update = self._build_depth_update(message, event_time)
                self._depth_last_update = last_update
                self._depth_allow_skip = False

        if resync_reason is not None:
            self._trigger_depth_resync(
                buffer,
                reason=resync_reason,
                details=resync_details,
            )
            return

        if update is not None:
            buffer.push_data(update)

    def _trigger_depth_resync(
        self,
        buffer: StreamBuffer[DepthStreamData],
        reason: ResyncReason,
        details: str,
    ) -> None:
        self._logger.log_resync(reason, details)
        buffer.push_resync(reason, details)
        self._reset_depth_state()
        try:
            snapshot = self.fetch_orderbook_snapshot()
        except Exception as exc:  # noqa: BLE001
            buffer.push_resync(
                ResyncReason.CONNECTION_LOST,
                details=f"Ошибка получения снапшота: {exc}",
            )
            return
        self._apply_depth_snapshot(snapshot, buffer)

    def _reset_depth_state(self) -> None:
        with self._lock:
            self._depth_last_update = None
            self._depth_allow_skip = False
            self._depth_buffered_messages.clear()

    def _apply_depth_snapshot(
        self, snapshot: OrderBookSnapshot, buffer: StreamBuffer[DepthStreamData]
    ) -> None:
        with self._lock:
            self._depth_last_update = snapshot.last_update_id
            self._depth_allow_skip = True
            pending = tuple(self._depth_buffered_messages)
            self._depth_buffered_messages.clear()

        buffer.push_snapshot(snapshot)

        for message in pending:
            self._handle_depth_message(
                message,
                buffer,
                buffer_if_uninitialized=False,
                allow_skip=True,
            )

    def stream_book_ticker(self) -> StreamSubscription[BestBidAsk]:
        buffer: StreamBuffer[BestBidAsk] = StreamBuffer(
            name="book_ticker",
            logger=self._logger,
            silence_timeout=self._silence_timeout,
            heartbeat_interval=self._heartbeat_interval,
            drop_oldest_on_overflow=True,
        )

        def handle_message(message: dict[str, Any]) -> None:
            for payload in self._parse_book_ticker(message):
                buffer.push_data(payload)

        def handle_error(reason: ResyncReason, details: str) -> None:
            suffix = f"(symbol {self.symbol})"
            message = details if suffix in details else f"{details} {suffix}"
            should_log = not (
                reason == ResyncReason.CONNECTION_LOST
                and "ошибка чтения сокета" in details
            )
            buffer.push_resync(reason, message)
            if should_log:
                self._logger.log(message)

        release = self._stream_pool.register_book_ticker(
            self.symbol,
            buffer,
            handle_message,
            handle_error,
        )

        def iterator() -> Iterator[StreamEvent[BestBidAsk]]:
            try:
                while True:
                    yield buffer.next()
            finally:
                buffer.stop()
                release()

        return StreamSubscription(events=iterator(), _buffer=buffer, _stopper=release)

    def stream_trades(self) -> StreamSubscription[Trade]:
        buffer: StreamBuffer[Trade] = StreamBuffer(
            name="trades",
            logger=self._logger,
            silence_timeout=self._silence_timeout,
            heartbeat_interval=self._heartbeat_interval,
            maxsize=4096,
            drop_oldest_on_overflow=True,
        )

        def handle_message(message: dict[str, Any]) -> None:
            for payload in self._parse_trade(message):
                buffer.push_data(payload)

        def handle_error(reason: ResyncReason, details: str) -> None:
            suffix = f"(symbol {self.symbol})"
            message = details if suffix in details else f"{details} {suffix}"
            should_log = not (
                reason == ResyncReason.CONNECTION_LOST
                and "ошибка чтения сокета" in details
            )
            buffer.push_resync(reason, message)
            if should_log:
                self._logger.log(message)

        release = self._stream_pool.register_trades(
            self.symbol,
            buffer,
            handle_message,
            handle_error,
        )

        def iterator() -> Iterator[StreamEvent[Trade]]:
            try:
                while True:
                    yield buffer.next()
            finally:
                buffer.stop()
                release()

        return StreamSubscription(events=iterator(), _buffer=buffer, _stopper=release)

    def stream_kline_1m(self) -> StreamSubscription[Candle]:
        return self._run_simple_stream(
            name="kline_1m",
            url=f"{self._endpoints.ws_base}/{self.symbol.lower()}@kline_1m",
            parser=self._parse_kline,
        )

    def _run_simple_stream(
        self,
        name: str,
        url: str,
        parser: Callable[[dict[str, Any]], Iterable[Any]],
        *,
        drop_oldest_on_overflow: bool = False,
        maxsize: int | None = None,
    ) -> StreamSubscription[Any]:
        silence_timeout = self._silence_timeout
        heartbeat_interval = self._heartbeat_interval

        buffer: StreamBuffer[Any] = StreamBuffer(
            name=name,
            logger=self._logger,
            silence_timeout=silence_timeout,
            heartbeat_interval=heartbeat_interval,
            maxsize=maxsize,
            drop_oldest_on_overflow=drop_oldest_on_overflow,
        )

        worker = threading.Thread(
            target=self._stream_worker,
            args=(url, parser, buffer, name),
            name=f"binance-{name}-{self.symbol.lower()}",
            daemon=True,
        )
        worker.start()

        def iterator() -> Iterator[StreamEvent[Any]]:
            try:
                while True:
                    yield buffer.next()
            finally:
                buffer.stop()
                worker.join(timeout=1.0)

        return StreamSubscription(events=iterator(), _buffer=buffer, _worker=worker)

    def _stream_worker(
        self,
        url: str,
        parser: Callable[[dict[str, Any]], Iterable[Any]],
        buffer: StreamBuffer[Any],
        name: str,
    ) -> None:
        reconnect_after_silence = False
        while not buffer.stopped():
            ws: WebSocketClient | None = None
            try:
                if reconnect_after_silence:
                    self._logger.log(
                        f"Binance {name} stream: перезапуск соединения после тайм-аута тишины"
                    )
                else:
                    self._logger.log(f"Binance {name} stream: открываем соединение")
                ws, timeout_exception = self._connect_websocket(url)
                ws.settimeout(self._ws_timeout)
                if reconnect_after_silence:
                    self._logger.log(
                        f"Binance {name} stream: соединение успешно восстановлено"
                    )
                    reconnect_after_silence = False
                while not buffer.stopped():
                    if buffer.consume_restart_request():
                        reconnect_after_silence = True
                        self._logger.log(
                            f"Binance {name} stream: получен запрос перезапуска от буфера"
                        )
                        break
                    try:
                        raw = ws.recv()
                    except timeout_exception:
                        if buffer.consume_restart_request():
                            reconnect_after_silence = True
                            self._logger.log(
                                f"Binance {name} stream: перезапуск по запросу буфера после тайм-аута ожидания"
                            )
                            break
                        buffer.push(StreamEvent.heartbeat())
                        continue
                    if not raw:
                        if buffer.consume_restart_request():
                            reconnect_after_silence = True
                            self._logger.log(
                                f"Binance {name} stream: перезапуск по запросу буфера после пустого сообщения"
                            )
                            break
                        continue
                    message = json.loads(raw)
                    for payload in parser(message):
                        buffer.push_data(payload)
                    if buffer.consume_restart_request():
                        reconnect_after_silence = True
                        self._logger.log(
                            f"Binance {name} stream: перезапуск по запросу буфера после обработки сообщения"
                        )
                        break
            except Exception as exc:
                reason = (
                    ResyncReason.SILENCE_TIMEOUT
                    if reconnect_after_silence
                    else ResyncReason.CONNECTION_LOST
                )
                if reason == ResyncReason.SILENCE_TIMEOUT:
                    details = (
                        f"Binance {name} stream: не удалось переподключиться после тайм-аута тишины: {exc}"
                    )
                    buffer.push_resync(reason, details)
                    self._logger.log(details)
                    time.sleep(self._reconnect_delay)
                else:
                    details = f"Binance {name} stream: {exc}"
                    buffer.push_resync(reason, details)
                    self._logger.log(details)
                    time.sleep(self._reconnect_delay)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def _connect_websocket(
        self, url: str
    ) -> tuple["WebSocketClient", type[Exception]]:
        connection = ThreadedWebSocketClient(
            url,
            timeout=self._ws_timeout,
            heartbeat_interval=self._heartbeat_interval,
            heartbeat_timeout=self._ws_timeout,
        )
        return connection, WebSocketTimeoutError

    @staticmethod
    def _build_level(
            price: float, quantity: float, timestamp: datetime
    ) -> OrderBookLevel:
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

    def _build_depth_update(
        self, message: dict[str, Any], event_time: datetime
    ) -> OrderBookUpdate:
        bids = tuple(
            self._build_level(float(price), float(qty), event_time)
            for price, qty in message.get("b", [])
        )
        asks = tuple(
            self._build_level(float(price), float(qty), event_time)
            for price, qty in message.get("a", [])
        )
        first_update = int(message.get("U", 0))
        last_update = int(message.get("u", 0))
        return OrderBookUpdate(
            exchange=Exchange.BINANCE,
            symbol=self.symbol,
            first_update_id=first_update,
            last_update_id=last_update,
            bids=bids,
            asks=asks,
            event_time=event_time,
        )

    def _parse_book_ticker(self, message: dict[str, Any]) -> Iterable[BestBidAsk]:
        if message.get("s") != self.symbol:
            return ()
        event_time = from_exchange_timestamp(message.get("E", 0) / 1000.0)
        return (
            BestBidAsk(
                exchange=Exchange.BINANCE.value,
                symbol=self.symbol,
                bid_price=float(message.get("b", 0.0)),
                bid_quantity=float(message.get("B", 0.0)),
                ask_price=float(message.get("a", 0.0)),
                ask_quantity=float(message.get("A", 0.0)),
                event_time=event_time,
            ),
        )

    def _parse_trade(self, message: dict[str, Any]) -> Iterable[Trade]:
        if message.get("s") != self.symbol:
            return ()
        event_time = from_exchange_timestamp(message.get("T", 0) / 1000.0)
        side = Side.ASK if message.get("m", False) else Side.BID
        return (
            Trade(
                trade_id=str(message.get("a", message.get("t", ""))),
                exchange=Exchange.BINANCE,
                symbol=self.symbol,
                executed_at=event_time,
                price=float(message.get("p", 0.0)),
                quantity=float(message.get("q", 0.0)),
                side=side,
            ),
        )

    def _parse_kline(self, message: dict[str, Any]) -> Iterable[Candle]:
        if message.get("e") != "kline":
            return ()
        payload = message.get("k") or {}
        if payload.get("s") != self.symbol:
            return ()
        open_time = from_exchange_timestamp(payload.get("t", 0) / 1000.0)
        close_time = from_exchange_timestamp(payload.get("T", 0) / 1000.0)
        return (
            Candle(
                open_time=open_time,
                close_time=close_time,
                open_price=float(payload.get("o", 0.0)),
                high_price=float(payload.get("h", 0.0)),
                low_price=float(payload.get("l", 0.0)),
                close_price=float(payload.get("c", 0.0)),
                volume=float(payload.get("v", 0.0)),
                quote_volume=float(payload.get("q", 0.0)),
            ),
        )


__all__ = ["BinanceExchangeData"]
