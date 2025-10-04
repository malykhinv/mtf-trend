from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.client import RemoteDisconnected
from typing import (
    Any,
    Callable,
    Iterable,
    Iterator,
    Optional,
    Protocol,
    Sequence,
    TypeAlias,
    TypedDict,
    cast,
)
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


@dataclass(slots=True)
class BybitEndpoints:
    rest_base: str = "https://api.bybit.com"
    ws_base: str = "wss://stream.bybit.com/v5/public/linear"


class DepthLevelMap(TypedDict, total=False):
    price: float | str | int
    p: float | str | int
    px: float | str | int
    Px: float | str | int
    size: float | str | int
    qty: float | str | int
    v: float | str | int
    quantity: float | str | int


DepthLevelEntry: TypeAlias = DepthLevelMap | list[Any] | tuple[Any, ...]


class DepthUpdatePayload(TypedDict, total=False):
    b: Sequence[DepthLevelEntry]
    bids: Sequence[DepthLevelEntry]
    a: Sequence[DepthLevelEntry]
    asks: Sequence[DepthLevelEntry]
    seq: int | str | None
    u: int | str | None
    prevSeq: int | str | None
    pu: int | str | None
    ts: int | float | str | None


class DepthStreamMessage(TypedDict, total=False):
    type: str
    ts: int | float | str | None
    data: Sequence[DepthUpdatePayload] | DepthUpdatePayload | None


@dataclass(slots=True)
class DepthEnvelope:
    payload: DepthUpdatePayload
    sequence: int
    previous_sequence: int
    event_time: datetime


class WebSocketClient(Protocol):
    def recv(self) -> str: ...

    def send(self, data: str) -> None: ...

    def close(self) -> None: ...

    def settimeout(self, timeout: float) -> None: ...


class BybitExchangeData:
    def __init__(
        self,
        symbol: str,
        loop_interval_ms: int = 100,
        depth_limit: int = 200,
        rest_timeout: float = 5.0,
        rest_retries: int = 3,
        rest_retry_delay: float = 0.5,
        rest_retry_backoff: float = 2.0,
        ws_timeout: float = 10.0,
        reconnect_delay: float = 1.0,
        log_writer: Optional[Callable[[str], None]] = None,
        endpoints: Optional[BybitEndpoints] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        self.symbol = symbol.upper()
        self._endpoints = endpoints or BybitEndpoints()
        self._rest_timeout = rest_timeout
        self._rest_retries = max(0, int(rest_retries))
        self._rest_retry_delay = max(0.0, float(rest_retry_delay))
        self._rest_retry_backoff = max(1.0, float(rest_retry_backoff))
        self._ws_timeout = ws_timeout
        self._reconnect_delay = reconnect_delay
        self._depth_limit = depth_limit
        self._logger = ExchangeLogger(f"Bybit:{self.symbol}", log_writer)
        self._silence_timeout = max(0.1, (loop_interval_ms * 5) / 1000.0)
        self._heartbeat_interval = self._silence_timeout / 2
        self._depth_last_seq: Optional[int] = None
        self._lock = threading.Lock()
        self._api_key = api_key
        self._api_secret = api_secret

    def _rest_get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        params = params or {}
        query = parse.urlencode(params)
        url = f"{self._endpoints.rest_base}{path}"
        if query:
            url = f"{url}?{query}"
        req = Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        data = self._execute_rest_request(req)
        if data.get("retCode") not in (0, None):
            raise RuntimeError(f"Bybit API error: {data.get('retMsg')}")
        return data

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
        data = self._rest_get(
            "/v5/market/instruments-info",
            {"category": "linear", "symbol": self.symbol},
        )
        symbols = (data.get("result") or {}).get("list") or []
        if not symbols:
            raise ValueError(f"Symbol {self.symbol} not found in instruments-info response")
        info = symbols[0]
        lot = info.get("lotSizeFilter", {})
        price = info.get("priceFilter", {})
        min_notional = info.get("minOrderValue") or lot.get("minOrderQty") or 0
        return SymbolFilters(
            exchange=Exchange.BYBIT,
            symbol=self.symbol,
            base_asset=info.get("baseCoin", ""),
            quote_asset=info.get("quoteCoin", ""),
            price_tick_size=float(price.get("tickSize", "0.0")),
            quantity_step_size=float(lot.get("qtyStep", "0.0")),
            min_price=float(price.get("minPrice", "0.0")),
            max_price=float(price.get("maxPrice", "0.0")),
            min_qty=float(lot.get("minOrderQty", "0.0")),
            max_qty=float(lot.get("maxOrderQty", "0.0")),
            min_notional=float(min_notional),
        )

    def fetch_orderbook_snapshot(self) -> OrderBookSnapshot:
        data = self._rest_get(
            "/v5/market/orderbook",
            {"category": "linear", "symbol": self.symbol, "limit": self._depth_limit},
        )
        result = data.get("result") or {}
        bids_raw = result.get("b") or result.get("bids") or []
        asks_raw = result.get("a") or result.get("asks") or []
        seq = int(result.get("u") or result.get("seq") or 0)
        now = get_current_time()
        bids = tuple(
            self._build_level(price, quantity, now)
            for price, quantity in self._normalize_levels(bids_raw)
        )
        asks = tuple(
            self._build_level(price, quantity, now)
            for price, quantity in self._normalize_levels(asks_raw)
        )
        snapshot = OrderBookSnapshot(
            exchange=Exchange.BYBIT,
            symbol=self.symbol,
            last_update_id=seq,
            bids=bids,
            asks=asks,
            received_at=now,
        )
        with self._lock:
            self._depth_last_seq = seq
        return snapshot

    def fetch_next_funding_time(self) -> Optional[datetime]:
        response = self._rest_get(
            "/v5/market/funding/history",
            {"category": "linear", "symbol": self.symbol, "limit": 1},
        )
        result = response.get("result") if isinstance(response, dict) else None
        records = [] if result is None else result.get("list") or []
        if not records:
            return None
        entry = records[0]
        next_funding_raw = entry.get("nextFundingTime") or entry.get("fundingRateTimestamp")
        if next_funding_raw is None:
            return None
        candidate = from_exchange_timestamp(next_funding_raw)
        interval = timedelta(hours=8)
        if interval <= timedelta(0):
            return None
        now = get_current_time()
        if candidate <= now:
            candidate += interval
            while candidate <= now:
                candidate += interval
        return candidate

    def stream_depth(self) -> StreamSubscription[DepthStreamData]:
        buffer: StreamBuffer[DepthStreamData] = StreamBuffer(
            name="depth",
            logger=self._logger,
            silence_timeout=self._silence_timeout,
            heartbeat_interval=self._heartbeat_interval,
        )
        initial_snapshot = self.fetch_orderbook_snapshot()
        buffer.push_snapshot(initial_snapshot)

        worker = threading.Thread(
            target=self._run_depth_stream,
            args=(buffer,),
            name=f"bybit-depth-{self.symbol.lower()}",
            daemon=True,
        )
        worker.start()

        def iterator() -> Iterator[StreamEvent[DepthStreamData]]:
            try:
                while True:
                    yield buffer.next()
            finally:
                buffer.stop()
                worker.join(timeout=1.0)

        return StreamSubscription(events=iterator(), _buffer=buffer, _worker=worker)

    def _run_depth_stream(self, buffer: StreamBuffer[DepthStreamData]) -> None:
        url = self._endpoints.ws_base
        topic = f"orderbook.50.{self.symbol}"
        subscribe = json.dumps({"op": "subscribe", "args": [topic]})
        reconnect_after_silence = False
        while not buffer.stopped():
            ws: WebSocketClient | None = None
            try:
                if reconnect_after_silence:
                    self._logger.log(
                        "Bybit depth stream: перезапуск соединения после тайм-аута тишины"
                    )
                else:
                    self._logger.log("Bybit depth stream: открываем соединение")
                ws, timeout_exception = self._connect_websocket(url)
                ws.settimeout(self._ws_timeout)
                ws.send(subscribe)
                if reconnect_after_silence:
                    self._logger.log("Bybit depth stream: соединение успешно восстановлено")
                    reconnect_after_silence = False
                while not buffer.stopped():
                    if buffer.consume_restart_request():
                        reconnect_after_silence = True
                        self._logger.log(
                            "Bybit depth stream: получен запрос перезапуска от буфера"
                        )
                        break
                    try:
                        raw = ws.recv()
                    except timeout_exception:
                        if buffer.consume_restart_request():
                            reconnect_after_silence = True
                            self._logger.log(
                                "Bybit depth stream: перезапуск по запросу буфера после тайм-аута ожидания"
                            )
                            break
                        buffer.push(StreamEvent.heartbeat())
                        continue
                    if not raw:
                        if buffer.consume_restart_request():
                            reconnect_after_silence = True
                            self._logger.log(
                                "Bybit depth stream: перезапуск по запросу буфера после пустого сообщения"
                            )
                            break
                        continue
                    message = json.loads(raw)
                    if message.get("op") == "ping":
                        ws.send(json.dumps({"op": "pong", "req_id": message.get("req_id")}))
                        if buffer.consume_restart_request():
                            reconnect_after_silence = True
                            self._logger.log(
                                "Bybit depth stream: перезапуск по запросу буфера после ответа на ping"
                            )
                            break
                        continue
                    if message.get("topic") != topic:
                        if buffer.consume_restart_request():
                            reconnect_after_silence = True
                            self._logger.log(
                                "Bybit depth stream: перезапуск по запросу буфера при ожидании целевой темы"
                            )
                            break
                        continue
                    self._handle_depth_message(message, buffer)
                    if buffer.consume_restart_request():
                        reconnect_after_silence = True
                        self._logger.log(
                            "Bybit depth stream: перезапуск по запросу буфера после обработки сообщения"
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
                        "Bybit depth stream: не удалось переподключиться после тайм-аута тишины: "
                        f"{exc}"
                    )
                    buffer.push_resync(reason, details)
                    self._logger.log(details)
                    time.sleep(self._reconnect_delay)
                else:
                    details = f"Bybit depth stream: {exc}"
                    buffer.push_resync(reason, details)
                    self._logger.log_resync(reason, details)
                    time.sleep(self._reconnect_delay)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def _handle_depth_message(
        self, message: DepthStreamMessage, buffer: StreamBuffer[DepthStreamData]
    ) -> None:
        envelope = self._parse_depth_envelope(message)
        if envelope is None:
            return
        if message.get("type") == "snapshot":
            snapshot = self._build_snapshot_from_ws(envelope.payload, envelope.event_time)
            with self._lock:
                self._depth_last_seq = envelope.sequence
            buffer.push_snapshot(snapshot)
            return
        with self._lock:
            expected = None if self._depth_last_seq is None else self._depth_last_seq + 1
            if self._depth_last_seq is None:
                return
            if expected is not None and envelope.sequence < expected:
                return
            if expected is not None and envelope.sequence > expected:
                self._trigger_depth_resync(
                    buffer,
                    reason=ResyncReason.SEQUENCE_GAP,
                    details=f"Ожидали {expected}, получили {envelope.sequence}",
                )
                return
            if envelope.previous_sequence and envelope.previous_sequence != self._depth_last_seq:
                self._trigger_depth_resync(
                    buffer,
                    reason=ResyncReason.SEQUENCE_GAP,
                    details=f"Предыдущий seq {envelope.previous_sequence} != {self._depth_last_seq}",
                )
                return
            update = self._build_update_from_ws(envelope.payload, envelope.event_time)
            self._depth_last_seq = envelope.sequence
        buffer.push_data(update)

    def _parse_depth_envelope(
        self, message: DepthStreamMessage
    ) -> DepthEnvelope | None:
        match message.get("data"):
            case list() as data if data:
                candidate = data[0]
            case tuple() as data if data:
                candidate = data[0]
            case dict() as candidate:
                pass
            case _:
                return None

        match candidate:
            case dict() as payload_dict:
                payload = cast(DepthUpdatePayload, payload_dict)
            case _:
                return None

        sequence = self._coerce_sequence_number(
            message_value=payload.get("seq"),
            alternate_value=payload.get("u"),
            default=0,
        )
        previous_sequence = self._coerce_sequence_number(
            message_value=payload.get("prevSeq"),
            alternate_value=payload.get("pu"),
            default=sequence - 1,
        )
        timestamp_ms = self._resolve_timestamp_ms(
            message.get("ts"),
            payload.get("ts"),
        )
        event_time = from_exchange_timestamp(timestamp_ms / 1000.0)
        return DepthEnvelope(
            payload=payload,
            sequence=sequence,
            previous_sequence=previous_sequence,
            event_time=event_time,
        )

    def _connect_websocket(
        self, url: str
    ) -> tuple[WebSocketClient, type[Exception]]:
        from websocket import WebSocketTimeoutException, create_connection

        connection = create_connection(url, timeout=self._ws_timeout, enable_multithread=True)
        return connection, WebSocketTimeoutException

    @staticmethod
    def _coerce_sequence_number(
            message_value: Any,
        alternate_value: Any,
        default: int,
    ) -> int:
        for candidate in (message_value, alternate_value):
            match candidate:
                case int() as number:
                    return number
                case float() as number:
                    return int(number)
                case str() as text:
                    stripped = text.strip()
                    if not stripped:
                        continue
                    if stripped.isdigit():
                        return int(stripped)
                    try:
                        return int(float(stripped))
                    except ValueError:
                        continue
                case _:
                    continue
        return default

    @staticmethod
    def _resolve_timestamp_ms(*candidates: Any) -> int:
        for candidate in candidates:
            match candidate:
                case int() as number:
                    return number
                case float() as number:
                    return int(number)
                case str() as text:
                    stripped = text.strip()
                    if not stripped:
                        continue
                    if stripped.isdigit():
                        return int(stripped)
                    try:
                        return int(float(stripped))
                    except ValueError:
                        continue
                case _:
                    continue
        return 0

    def _trigger_depth_resync(
        self,
        buffer: StreamBuffer[DepthStreamData],
        reason: ResyncReason,
        details: str,
    ) -> None:
        self._logger.log_resync(reason, details)
        buffer.push_resync(reason, details)
        try:
            snapshot = self.fetch_orderbook_snapshot()
        except Exception as exc:
            buffer.push_resync(
                ResyncReason.CONNECTION_LOST,
                details=f"Ошибка получения снапшота: {exc}",
            )
            return
        buffer.push_snapshot(snapshot)

    def stream_book_ticker(self) -> StreamSubscription[BestBidAsk]:
        topic = f"tickers.{self.symbol}"
        return self._run_simple_stream(
            name="book_ticker",
            topic=topic,
            parser=self._parse_ticker,
        )

    def stream_trades(self) -> StreamSubscription[Trade]:
        topic = f"publicTrade.{self.symbol}"
        return self._run_simple_stream(
            name="trades",
            topic=topic,
            parser=self._parse_trade,
        )

    def stream_kline_1m(self) -> StreamSubscription[Candle]:
        topic = f"kline.1.{self.symbol}"
        return self._run_simple_stream(
            name="kline_1m",
            topic=topic,
            parser=self._parse_kline,
        )

    def _run_simple_stream(
        self,
        name: str,
        topic: str,
        parser: Callable[[dict[str, Any]], Iterable[Any]],
    ) -> StreamSubscription[Any]:
        buffer: StreamBuffer[Any] = StreamBuffer(
            name=name,
            logger=self._logger,
            silence_timeout=self._silence_timeout,
            heartbeat_interval=self._heartbeat_interval,
        )
        subscribe = json.dumps({"op": "subscribe", "args": [topic]})

        worker = threading.Thread(
            target=self._simple_worker,
            args=(topic, parser, buffer, name, subscribe),
            name=f"bybit-{name}-{self.symbol.lower()}",
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

    def _simple_worker(
        self,
        topic: str,
        parser: Callable[[dict[str, Any]], Iterable[Any]],
        buffer: StreamBuffer[Any],
        name: str,
        subscribe: str,
    ) -> None:
        url = self._endpoints.ws_base
        reconnect_after_silence = False
        while not buffer.stopped():
            ws: WebSocketClient | None = None
            try:
                if reconnect_after_silence:
                    self._logger.log(
                        f"Bybit {name} stream: перезапуск соединения после тайм-аута тишины"
                    )
                else:
                    self._logger.log(f"Bybit {name} stream: открываем соединение")
                ws, timeout_exception = self._connect_websocket(url)
                ws.settimeout(self._ws_timeout)
                ws.send(subscribe)
                if reconnect_after_silence:
                    self._logger.log(
                        f"Bybit {name} stream: соединение успешно восстановлено"
                    )
                    reconnect_after_silence = False
                while not buffer.stopped():
                    if buffer.consume_restart_request():
                        reconnect_after_silence = True
                        self._logger.log(
                            f"Bybit {name} stream: получен запрос перезапуска от буфера"
                        )
                        break
                    try:
                        raw = ws.recv()
                    except timeout_exception:
                        if buffer.consume_restart_request():
                            reconnect_after_silence = True
                            self._logger.log(
                                f"Bybit {name} stream: перезапуск по запросу буфера после тайм-аута ожидания"
                            )
                            break
                        buffer.push(StreamEvent.heartbeat())
                        continue
                    if not raw:
                        if buffer.consume_restart_request():
                            reconnect_after_silence = True
                            self._logger.log(
                                f"Bybit {name} stream: перезапуск по запросу буфера после пустого сообщения"
                            )
                            break
                        continue
                    message = json.loads(raw)
                    if message.get("op") == "ping":
                        ws.send(json.dumps({"op": "pong", "req_id": message.get("req_id")}))
                        if buffer.consume_restart_request():
                            reconnect_after_silence = True
                            self._logger.log(
                                f"Bybit {name} stream: перезапуск по запросу буфера после ответа на ping"
                            )
                            break
                        continue
                    if message.get("topic") != topic:
                        if buffer.consume_restart_request():
                            reconnect_after_silence = True
                            self._logger.log(
                                f"Bybit {name} stream: перезапуск по запросу буфера при ожидании целевой темы"
                            )
                            break
                        continue
                    for payload in parser(message):
                        buffer.push_data(payload)
                    if buffer.consume_restart_request():
                        reconnect_after_silence = True
                        self._logger.log(
                            f"Bybit {name} stream: перезапуск по запросу буфера после обработки сообщения"
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
                        f"Bybit {name} stream: не удалось переподключиться после тайм-аута тишины: {exc}"
                    )
                    buffer.push_resync(reason, details)
                    self._logger.log(details)
                    time.sleep(self._reconnect_delay)
                else:
                    details = f"Bybit {name} stream: {exc}"
                    buffer.push_resync(reason, details)
                    self._logger.log(details)
                    time.sleep(self._reconnect_delay)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

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

    @staticmethod
    def _normalize_levels(
            entries: Iterable[DepthLevelEntry]
    ) -> Iterable[tuple[float, float]]:
        normalized: list[tuple[float, float]] = []
        for entry in entries:
            price_value: Any | None = None
            quantity_value: Any | None = None

            match entry:
                case str() | bytes():
                    continue
                case [price, quantity, *rest]:
                    price_value, quantity_value = price, quantity
                case (price, quantity, *rest):
                    price_value, quantity_value = price, quantity
                case _:
                    pass

            if price_value is None:
                match entry:
                    case {"price": price}:
                        price_value = price
                    case {"p": price}:
                        price_value = price
                    case {"px": price}:
                        price_value = price
                    case {"Px": price}:
                        price_value = price
                    case _:
                        pass

            if quantity_value is None:
                match entry:
                    case {"size": quantity}:
                        quantity_value = quantity
                    case {"qty": quantity}:
                        quantity_value = quantity
                    case {"v": quantity}:
                        quantity_value = quantity
                    case {"quantity": quantity}:
                        quantity_value = quantity
                    case _:
                        pass

            if price_value is None or quantity_value is None:
                continue
            try:
                normalized.append((float(price_value), float(quantity_value)))
            except (TypeError, ValueError):
                continue
        return normalized

    def _build_snapshot_from_ws(
        self, payload: DepthUpdatePayload, event_time: datetime
    ) -> OrderBookSnapshot:
        bids_raw = payload.get("b") or payload.get("bids") or []
        asks_raw = payload.get("a") or payload.get("asks") or []
        bids = tuple(
            self._build_level(price, quantity, event_time)
            for price, quantity in self._normalize_levels(bids_raw)
        )
        asks = tuple(
            self._build_level(price, quantity, event_time)
            for price, quantity in self._normalize_levels(asks_raw)
        )
        seq = int(payload.get("seq") or payload.get("u") or 0)
        return OrderBookSnapshot(
            exchange=Exchange.BYBIT,
            symbol=self.symbol,
            last_update_id=seq,
            bids=bids,
            asks=asks,
            received_at=event_time,
        )

    def _build_update_from_ws(
        self, payload: DepthUpdatePayload, event_time: datetime
    ) -> OrderBookUpdate:
        bids_raw = payload.get("b") or payload.get("bids") or []
        asks_raw = payload.get("a") or payload.get("asks") or []
        bids = tuple(
            self._build_level(price, quantity, event_time)
            for price, quantity in self._normalize_levels(bids_raw)
        )
        asks = tuple(
            self._build_level(price, quantity, event_time)
            for price, quantity in self._normalize_levels(asks_raw)
        )
        first_seq = int(payload.get("prevSeq") or payload.get("pu") or payload.get("seq") or 0)
        last_seq = int(payload.get("seq") or payload.get("u") or first_seq)
        return OrderBookUpdate(
            exchange=Exchange.BYBIT,
            symbol=self.symbol,
            first_update_id=first_seq,
            last_update_id=last_seq,
            bids=bids,
            asks=asks,
            event_time=event_time,
        )

    def _parse_ticker(self, message: dict[str, Any]) -> Iterable[BestBidAsk]:
        data = message.get("data") or []
        if isinstance(data, dict):
            entries = [data]
        else:
            entries = list(data)
        results: list[BestBidAsk] = []
        for payload in entries:
            event_time = from_exchange_timestamp(
                (payload.get("ts") or message.get("ts") or 0) / 1000.0
            )
            results.append(
                BestBidAsk(
                    exchange=Exchange.BYBIT.value,
                    symbol=self.symbol,
                    bid_price=float(payload.get("bid1Price") or payload.get("bidPrice") or 0.0),
                    bid_quantity=float(payload.get("bid1Size") or payload.get("bidSize") or 0.0),
                    ask_price=float(payload.get("ask1Price") or payload.get("askPrice") or 0.0),
                    ask_quantity=float(payload.get("ask1Size") or payload.get("askSize") or 0.0),
                    event_time=event_time,
                )
            )
        return tuple(results)

    def _parse_trade(self, message: dict[str, Any]) -> Iterable[Trade]:
        data = message.get("data") or []
        if isinstance(data, dict):
            entries = [data]
        else:
            entries = list(data)
        trades: list[Trade] = []
        for payload in entries:
            event_time = from_exchange_timestamp(payload.get("T", 0) / 1000.0)
            side_value = payload.get("S") or payload.get("side")
            side = Side.BID if str(side_value).lower() in ("buy", "bid", "true") else Side.ASK
            trades.append(
                Trade(
                    trade_id=str(payload.get("i") or payload.get("execId") or payload.get("id")),
                    exchange=Exchange.BYBIT,
                    symbol=self.symbol,
                    executed_at=event_time,
                    price=float(payload.get("p") or payload.get("price") or 0.0),
                    quantity=float(payload.get("v") or payload.get("size") or 0.0),
                    side=side,
                )
            )
        return tuple(trades)

    @staticmethod
    def _parse_kline(message: dict[str, Any]) -> Iterable[Candle]:
        data = message.get("data") or []
        if isinstance(data, dict):
            entries = [data]
        else:
            entries = list(data)
        candles: list[Candle] = []
        for payload in entries:
            open_raw = payload.get("start") or payload.get("t") or payload.get("openTime") or 0
            close_raw = payload.get("end") or payload.get("T") or payload.get("closeTime") or 0
            open_time = from_exchange_timestamp(open_raw / 1000.0)
            close_time = from_exchange_timestamp(close_raw / 1000.0)
            candles.append(
                Candle(
                    open_time=open_time,
                    close_time=close_time,
                    open_price=float(payload.get("open") or payload.get("o") or 0.0),
                    high_price=float(payload.get("high") or payload.get("h") or 0.0),
                    low_price=float(payload.get("low") or payload.get("l") or 0.0),
                    close_price=float(payload.get("close") or payload.get("c") or 0.0),
                    volume=float(payload.get("volume") or payload.get("v") or 0.0),
                    quote_volume=float(payload.get("turnover") or payload.get("q") or 0.0),
                )
            )
        return tuple(candles)


__all__ = ["BybitExchangeData"]
