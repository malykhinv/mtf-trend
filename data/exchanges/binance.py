from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Optional
from urllib import parse, request

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

from .base import (
    BestBidAsk,
    DepthStreamData,
    ExchangeLogger,
    ResyncReason,
    StreamBuffer,
    StreamEvent,
)


@dataclass(slots=True)
class BinanceEndpoints:
    rest_base: str = "https://fapi.binance.com"
    ws_base: str = "wss://fstream.binance.com/ws"


class BinanceExchangeData:
    def __init__(
        self,
        symbol: str,
        loop_interval_ms: int = 100,
        depth_limit: int = 200,
        rest_timeout: float = 5.0,
        ws_timeout: float = 10.0,
        reconnect_delay: float = 1.0,
        log_writer: Optional[Callable[[str], None]] = None,
        endpoints: Optional[BinanceEndpoints] = None,
    ) -> None:
        self.symbol = symbol.upper()
        self._endpoints = endpoints or BinanceEndpoints()
        self._rest_timeout = rest_timeout
        self._ws_timeout = ws_timeout
        self._reconnect_delay = reconnect_delay
        self._depth_limit = depth_limit
        self._logger = ExchangeLogger(f"Binance:{self.symbol}", log_writer)
        self._silence_timeout = max(0.1, (loop_interval_ms * 5) / 1000.0)
        self._heartbeat_interval = self._silence_timeout / 2
        self._depth_last_update: Optional[int] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------
    def _rest_get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        params = params or {}
        query = parse.urlencode(params)
        url = f"{self._endpoints.rest_base}{path}"
        if query:
            url = f"{url}?{query}"
        req = request.Request(url, method="GET", headers={"User-Agent": "mtf-trend/1.0"})
        with request.urlopen(req, timeout=self._rest_timeout) as resp:  # type: ignore[arg-type]
            payload = resp.read().decode("utf-8")
        return json.loads(payload)

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
        now = datetime.now(timezone.utc)
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
        with self._lock:
            self._depth_last_update = last_update_id
        return snapshot

    # ------------------------------------------------------------------
    # Streaming interfaces
    # ------------------------------------------------------------------
    def stream_depth(self) -> Iterator[StreamEvent[DepthStreamData]]:
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
            name=f"binance-depth-{self.symbol.lower()}",
            daemon=True,
        )
        worker.start()
        try:
            while True:
                yield buffer.next()
        finally:
            buffer.stop()
            worker.join(timeout=1.0)

    def _run_depth_stream(self, buffer: StreamBuffer[DepthStreamData]) -> None:
        url = f"{self._endpoints.ws_base}/{self.symbol.lower()}@depth@100ms"
        while not buffer.stopped():
            ws = None
            try:
                from websocket import WebSocketTimeoutException, create_connection  # type: ignore

                ws = create_connection(url, timeout=self._ws_timeout, enable_multithread=True)
                ws.settimeout(self._ws_timeout)
                while not buffer.stopped():
                    try:
                        raw = ws.recv()
                    except WebSocketTimeoutException:
                        buffer.push(StreamEvent.heartbeat())
                        continue
                    if not raw:
                        continue
                    message = json.loads(raw)
                    self._handle_depth_message(message, buffer)
            except Exception as exc:  # pragma: no cover - defensive
                buffer.push_resync(
                    ResyncReason.CONNECTION_LOST,
                    details=f"Binance depth stream: {exc}",
                )
                self._logger.log_resync(
                    ResyncReason.CONNECTION_LOST,
                    details=f"Binance depth stream: {exc}",
                )
                time.sleep(self._reconnect_delay)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def _handle_depth_message(
        self, message: dict[str, Any], buffer: StreamBuffer[DepthStreamData]
    ) -> None:
        if message.get("e") != "depthUpdate":
            return
        first_update = int(message.get("U", 0))
        last_update = int(message.get("u", 0))
        prev_update = int(message.get("pu", first_update - 1))
        event_time = datetime.fromtimestamp(message.get("E", 0) / 1000.0, tz=timezone.utc)

        with self._lock:
            expected = None if self._depth_last_update is None else self._depth_last_update + 1
            if self._depth_last_update is None:
                if last_update == 0:
                    return
                self._depth_last_update = last_update
            elif last_update <= self._depth_last_update:
                return
            elif expected is not None and first_update > expected:
                self._trigger_depth_resync(
                    buffer,
                    reason=ResyncReason.SEQUENCE_GAP,
                    details=f"Ожидали {expected}, получили диапазон {first_update}-{last_update}",
                )
                return
            elif prev_update != self._depth_last_update:
                self._trigger_depth_resync(
                    buffer,
                    reason=ResyncReason.SEQUENCE_GAP,
                    details=f"Предыдущий апдейт {prev_update} != {self._depth_last_update}",
                )
                return
            update = self._build_depth_update(message, event_time)
            self._depth_last_update = last_update
        buffer.push_data(update)

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
        except Exception as exc:  # pragma: no cover - defensive
            buffer.push_resync(
                ResyncReason.CONNECTION_LOST,
                details=f"Ошибка получения снапшота: {exc}",
            )
            return
        buffer.push_snapshot(snapshot)

    def stream_book_ticker(self) -> Iterator[StreamEvent[BestBidAsk]]:
        return self._run_simple_stream(
            name="book_ticker",
            url=f"{self._endpoints.ws_base}/{self.symbol.lower()}@bookTicker",
            parser=self._parse_book_ticker,
        )

    def stream_trades(self) -> Iterator[StreamEvent[Trade]]:
        return self._run_simple_stream(
            name="trades",
            url=f"{self._endpoints.ws_base}/{self.symbol.lower()}@aggTrade",
            parser=self._parse_trade,
        )

    def stream_kline_1m(self) -> Iterator[StreamEvent[Candle]]:
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
    ) -> Iterator[StreamEvent[Any]]:
        buffer: StreamBuffer[Any] = StreamBuffer(
            name=name,
            logger=self._logger,
            silence_timeout=self._silence_timeout,
            heartbeat_interval=self._heartbeat_interval,
        )

        worker = threading.Thread(
            target=self._stream_worker,
            args=(url, parser, buffer, name),
            name=f"binance-{name}-{self.symbol.lower()}",
            daemon=True,
        )
        worker.start()
        try:
            while True:
                yield buffer.next()
        finally:
            buffer.stop()
            worker.join(timeout=1.0)

    def _stream_worker(
        self,
        url: str,
        parser: Callable[[dict[str, Any]], Iterable[Any]],
        buffer: StreamBuffer[Any],
        name: str,
    ) -> None:
        while not buffer.stopped():
            ws = None
            try:
                from websocket import WebSocketTimeoutException, create_connection  # type: ignore

                ws = create_connection(url, timeout=self._ws_timeout, enable_multithread=True)
                ws.settimeout(self._ws_timeout)
                while not buffer.stopped():
                    try:
                        raw = ws.recv()
                    except WebSocketTimeoutException:
                        buffer.push(StreamEvent.heartbeat())
                        continue
                    if not raw:
                        continue
                    message = json.loads(raw)
                    for payload in parser(message):
                        buffer.push_data(payload)
            except Exception as exc:  # pragma: no cover - defensive
                buffer.push_resync(
                    ResyncReason.CONNECTION_LOST,
                    details=f"Binance {name} stream: {exc}",
                )
                time.sleep(self._reconnect_delay)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------
    def _build_level(
        self, price: float, quantity: float, timestamp: datetime
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
        event_time = datetime.fromtimestamp(message.get("E", 0) / 1000.0, tz=timezone.utc)
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
        event_time = datetime.fromtimestamp(message.get("T", 0) / 1000.0, tz=timezone.utc)
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
        open_time = datetime.fromtimestamp(payload.get("t", 0) / 1000.0, tz=timezone.utc)
        close_time = datetime.fromtimestamp(payload.get("T", 0) / 1000.0, tz=timezone.utc)
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
