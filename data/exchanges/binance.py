from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.client import RemoteDisconnected
from typing import Any, Callable, ClassVar, Iterable, Iterator, Optional, Sequence, cast
from urllib import parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from application.resync_coordinator import ResyncCoordinator, ResyncTask
from config.models.trading_profile import TradingProfile
from config.stream_limits import DEFAULT_BINANCE_STREAM_PROFILES
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
from .binance_stream_manager import BinanceStreamManager
from streams import (
    BookTickerStreamPipeline,
    DepthStreamPipeline,
    TradesStreamPipeline,
)


DepthMessage = dict[str, Any]
DeltaHandler = Callable[[DepthMessage], None]
WebSocketClient = ThreadedWebSocketClient


def fetch_snapshot(
    *,
    rest_get: Callable[[str, dict[str, Any]], Any],
    symbol: str,
    depth_limit: int,
    level_builder: Callable[[float, float, datetime], OrderBookLevel],
    now_provider: Callable[[], datetime] = get_current_time,
) -> OrderBookSnapshot:
    data = rest_get("/fapi/v1/depth", {"symbol": symbol, "limit": depth_limit})
    last_update_id = int(data["lastUpdateId"])
    now = now_provider()
    bids = tuple(
        level_builder(float(price), float(qty), now) for price, qty in data.get("bids", [])
    )
    asks = tuple(
        level_builder(float(price), float(qty), now) for price, qty in data.get("asks", [])
    )
    return OrderBookSnapshot(
        exchange=Exchange.BINANCE,
        symbol=symbol,
        last_update_id=last_update_id,
        bids=bids,
        asks=asks,
        received_at=now,
    )


def apply_snapshot(
    *, pipeline: DepthStreamPipeline, snapshot: OrderBookSnapshot
) -> None:
    pipeline.reset()
    pipeline.push_snapshot(snapshot)


def replay_delta(
    messages: Sequence[DepthMessage], handler: DeltaHandler
) -> None:
    for message in messages:
        handler(message)


@dataclass(slots=True)
class BinanceEndpoints:
    rest_base: str = "https://fapi.binance.com"
    ws_base: str = "wss://fstream.binance.com/ws"

BINANCE_ALLOWED_DEPTH_LIMITS: frozenset[int] = frozenset({5, 10, 20, 50, 100, 500, 1000})
MIN_STREAM_SILENCE_TIMEOUT_MS = 1500.0


@dataclass(frozen=True)
class _StreamTimeoutConfig:
    depth: float
    trades: float
    book_ticker: float


_PROFILE_STREAM_TIMEOUTS: dict[TradingProfile, _StreamTimeoutConfig] = {
    TradingProfile.TOP: _StreamTimeoutConfig(depth=2.5, trades=6.0, book_ticker=6.0),
    TradingProfile.ALT: _StreamTimeoutConfig(depth=3.5, trades=8.0, book_ticker=8.0),
    TradingProfile.LISTING: _StreamTimeoutConfig(depth=1.5, trades=5.0, book_ticker=5.0),
    TradingProfile.AUTO: _StreamTimeoutConfig(depth=3.0, trades=7.0, book_ticker=7.0),
}


_STREAM_PROFILE_WEIGHTS: dict[TradingProfile, dict[str, float]] = {
    TradingProfile.TOP: {"depth": 3.0, "trades": 2.5, "book": 2.0},
    TradingProfile.ALT: {"depth": 1.0, "trades": 1.0, "book": 0.8},
    TradingProfile.LISTING: {"depth": 2.5, "trades": 3.0, "book": 1.5},
    TradingProfile.AUTO: {"depth": 1.5, "trades": 1.5, "book": 1.0},
}


_RESTART_GRACE_FACTORS: dict[TradingProfile, dict[str, float]] = {
    TradingProfile.TOP: {"depth": 0.5, "trades": 0.7, "book": 0.7},
    TradingProfile.ALT: {"depth": 1.0, "trades": 1.2, "book": 1.2},
    TradingProfile.LISTING: {"depth": 1.5, "trades": 1.6, "book": 1.6},
    TradingProfile.AUTO: {"depth": 0.9, "trades": 1.0, "book": 1.0},
}


class BinanceExchangeData:
    _manager_lock: ClassVar[threading.Lock] = threading.Lock()
    _shared_stream_managers: ClassVar[dict[tuple[str, int], BinanceStreamManager]] = {}

    def __init__(
        self,
        symbol: str,
        loop_interval_ms: int = 100,
        depth_stream_interval_ms: int = 250,
        depth_limit: int = 100,
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
        profile: TradingProfile | None = None,
        resync_coordinator: ResyncCoordinator | None = None,
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
        self._depth_stream_interval_ms = self._normalize_depth_stream_interval(
            depth_stream_interval_ms
        )
        self._depth_limit = self._normalize_depth_limit(depth_limit)
        self._stream_manager = self._get_stream_manager(
            endpoints=self._endpoints,
            depth_stream_interval_ms=self._depth_stream_interval_ms,
            ws_timeout=self._ws_timeout,
            reconnect_delay=self._reconnect_delay,
            log_writer=log_writer,
        )
        self._logger.log(
            (
                "Binance depth stream: интервал {interval} мс, лимит снапшота {limit}"
            ).format(interval=self._depth_stream_interval_ms, limit=self._depth_limit)
        )
        self._profile = profile or TradingProfile.AUTO
        base_timeout_ms = (
            max(float(silence_timeout_ms), MIN_STREAM_SILENCE_TIMEOUT_MS)
            if silence_timeout_ms is not None
            else MIN_STREAM_SILENCE_TIMEOUT_MS
        )
        base_timeout_s = max(0.1, base_timeout_ms / 1000.0)
        timeouts = self._resolve_stream_silence_timeouts(
            base_timeout_s,
            self._profile,
        )
        self._depth_silence_timeout = timeouts.depth
        self._trade_silence_timeout = timeouts.trades
        self._book_ticker_silence_timeout = timeouts.book_ticker
        self._generic_silence_timeout = max(
            MIN_STREAM_SILENCE_TIMEOUT_MS / 1000.0,
            min(base_timeout_s, max(timeouts.trades, timeouts.book_ticker)),
        )
        self._heartbeat_interval = max(
            min(
                self._depth_silence_timeout,
                self._trade_silence_timeout,
                self._book_ticker_silence_timeout,
            )
            / 2,
            0.1,
        )
        self._logger.log(
            (
                "Binance streams: таймауты тишины (профиль {profile}): "
                "depth={depth:.1f} c, trades={trades:.1f} c, book_ticker={ticker:.1f} c"
            ).format(
                profile=self._profile.value,
                depth=self._depth_silence_timeout,
                trades=self._trade_silence_timeout,
                ticker=self._book_ticker_silence_timeout,
            )
        )
        self._depth_last_update: Optional[int] = None
        self._depth_buffered_messages: list[dict[str, Any]] = []
        self._depth_allow_skip = False
        self._lock = threading.Lock()
        self._api_key = api_key
        self._api_secret = api_secret
        self._depth_pipeline: Optional[DepthStreamPipeline] = None
        self._trades_pipeline: Optional[TradesStreamPipeline] = None
        self._ticker_pipeline: Optional[BookTickerStreamPipeline] = None
        self._resync_coordinator = resync_coordinator

    def _normalize_depth_stream_interval(self, interval_ms: int) -> int:
        default_interval = 250
        minimum_interval = 100
        maximum_interval = 1000
        try:
            requested_interval = int(interval_ms)
        except (TypeError, ValueError):
            self._logger.log(
                (
                    "Binance depth stream interval {value!r} невалиден, используем {default} мс"
                ).format(value=interval_ms, default=default_interval)
            )
            return default_interval

        if requested_interval <= 0:
            self._logger.log(
                (
                    "Binance depth stream interval {value} мс не поддерживается, используем {default} мс"
                ).format(value=requested_interval, default=default_interval)
            )
            return default_interval

        if requested_interval < minimum_interval:
            self._logger.log(
                (
                    "Binance depth stream interval {value} мс слишком мал, используем {minimum} мс"
                ).format(value=requested_interval, minimum=minimum_interval)
            )
            return minimum_interval

        if requested_interval > maximum_interval:
            self._logger.log(
                (
                    "Binance depth stream interval {value} мс слишком велик, используем {maximum} мс"
                ).format(value=requested_interval, maximum=maximum_interval)
            )
            return maximum_interval

        return requested_interval

    def _normalize_depth_limit(self, depth_limit: int) -> int:
        default_limit = 100
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

    def _resolve_stream_silence_timeouts(
        self,
        base_timeout_s: float,
        profile: TradingProfile,
    ) -> _StreamTimeoutConfig:
        template = _PROFILE_STREAM_TIMEOUTS.get(profile)
        if template is None:
            template = _PROFILE_STREAM_TIMEOUTS[TradingProfile.AUTO]
        min_timeout_s = max(0.1, MIN_STREAM_SILENCE_TIMEOUT_MS / 1000.0)
        return _StreamTimeoutConfig(
            depth=max(min_timeout_s, min(template.depth, base_timeout_s)),
            trades=max(min_timeout_s, min(template.trades, base_timeout_s)),
            book_ticker=max(min_timeout_s, min(template.book_ticker, base_timeout_s)),
        )

    def _stream_weight(self, stream_type: str) -> float:
        profile_weights = _STREAM_PROFILE_WEIGHTS.get(self._profile, {})
        fallback = _STREAM_PROFILE_WEIGHTS[TradingProfile.AUTO]
        weight = profile_weights.get(stream_type, fallback.get(stream_type, 1.0))
        return max(float(weight), 0.1)

    def _resolve_restart_grace_period(self, stream_type: str) -> float:
        factors = _RESTART_GRACE_FACTORS.get(self._profile, _RESTART_GRACE_FACTORS[TradingProfile.AUTO])
        factor = max(float(factors.get(stream_type, 1.0)), 0.1)
        if stream_type == "depth":
            base = self._depth_silence_timeout
        elif stream_type == "trades":
            base = self._trade_silence_timeout
        else:
            base = self._book_ticker_silence_timeout
        return max(0.5, base * factor)

    def _request_stream_migration(
        self,
        stream_type: str,
        reason: ResyncReason,
        details: str,
    ) -> None:
        message = details or reason.value
        self._logger.log(
            (
                "Binance {stream} stream: хроничная деградация символа {symbol}, попытка миграции: {message}"
            ).format(stream=stream_type, symbol=self.symbol, message=message)
        )
        try:
            migrated = self._stream_manager.migrate_stream(stream_type, self.symbol, message)
        except Exception as exc:  # noqa: BLE001
            self._logger.log(
                (
                    "Binance {stream} stream: ошибка миграции символа {symbol}: {error}"
                ).format(stream=stream_type, symbol=self.symbol, error=exc)
            )
            return
        if not migrated:
            self._logger.log(
                (
                    "Binance {stream} stream: миграция символа {symbol} не выполнена (нет доступных воркеров)"
                ).format(stream=stream_type, symbol=self.symbol)
            )

    @classmethod
    def _get_stream_manager(
        cls,
        *,
        endpoints: BinanceEndpoints,
        depth_stream_interval_ms: int,
        ws_timeout: float,
        reconnect_delay: float,
        log_writer: Optional[Callable[[str], None]],
    ) -> BinanceStreamManager:
        with cls._manager_lock:
            key = (endpoints.ws_base.rstrip("/"), depth_stream_interval_ms)
            manager = cls._shared_stream_managers.get(key)
            if manager is None:
                profiles = DEFAULT_BINANCE_STREAM_PROFILES
                expected_weights = {
                    stream: profile.max_streams_per_connection * 1.5
                    for stream, profile in profiles.items()
                }
                manager = BinanceStreamManager(
                    endpoints_ws_base=endpoints.ws_base,
                    ws_timeout=ws_timeout,
                    reconnect_delay=reconnect_delay,
                    log_writer=log_writer,
                    depth_stream_interval_ms=depth_stream_interval_ms,
                    expected_stream_weights=expected_weights,
                )
                cls._shared_stream_managers[key] = manager
            return manager

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
                socket.gaierror,
                ConnectionError,
                OSError,
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
        return fetch_snapshot(
            rest_get=self._rest_get,
            symbol=self.symbol,
            depth_limit=self._depth_limit,
            level_builder=self._build_level,
        )

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
        silence_timeout = self._depth_silence_timeout
        heartbeat_interval = max(silence_timeout / 2, 0.1)

        buffer: StreamBuffer[DepthStreamData] = StreamBuffer(
            name="depth",
            logger=self._logger,
            silence_timeout=silence_timeout,
            heartbeat_interval=heartbeat_interval,
            maxsize=DepthStreamPipeline.buffer_size(self._profile),
            drop_oldest_on_overflow=True,
            restart_grace_period=self._resolve_restart_grace_period("depth"),
        )
        pipeline = DepthStreamPipeline(
            name="depth",
            buffer=buffer,
            profile=self._profile,
            on_chronic_error=lambda reason, details: self._request_stream_migration(
                "depth", reason, details or "depth stream chronic degradation"
            ),
        )
        self._depth_pipeline = pipeline
        self._reset_depth_state()

        def handle_message(message: dict[str, Any]) -> None:
            self._handle_depth_message(message, pipeline)

        def handle_error(reason: ResyncReason, details: str) -> None:
            suffix = f"(symbol {self.symbol})"
            message = details if suffix in details else f"{details} {suffix}"
            should_log = not (
                reason == ResyncReason.CONNECTION_LOST
                and "ошибка чтения сокета" in details
            )
            pipeline.push_resync(reason, message)
            if should_log:
                self._logger.log(message)

        release = self._stream_manager.register_depth(
            self.symbol,
            pipeline.buffer,
            handle_message,
            handle_error,
            weight=self._stream_weight("depth"),
        )

        self._start_depth_snapshot(pipeline)

        def iterator() -> Iterator[StreamEvent[DepthStreamData]]:
            try:
                while True:
                    yield pipeline.next_event()
            finally:
                pipeline.buffer.stop()
                release()

        return StreamSubscription(events=iterator(), _buffer=pipeline.buffer, _stopper=release)

    def _start_depth_snapshot(self, pipeline: DepthStreamPipeline) -> None:
        buffer = pipeline.buffer

        def load_snapshot() -> None:
            while not buffer.stopped():
                try:
                    snapshot = self.fetch_orderbook_snapshot()
                except Exception as exc:  # noqa: BLE001
                    details = (
                        "Binance depth stream: не удалось получить начальный снапшот: "
                        f"{exc}"
                    )
                    pipeline.push_resync(ResyncReason.CONNECTION_LOST, details)
                    self._logger.log_resync(ResyncReason.CONNECTION_LOST, details)
                    self._reset_depth_state()
                    time.sleep(self._reconnect_delay)
                    continue
                self._apply_depth_snapshot(snapshot, pipeline)
                break

        threading.Thread(
            target=load_snapshot,
            name=f"binance-depth-snapshot-{self.symbol.lower()}",
            daemon=True,
        ).start()

    def _handle_depth_message(
        self,
        message: dict[str, Any],
        pipeline: DepthStreamPipeline,
        *,
        buffer_if_uninitialized: bool = True,
        allow_skip: bool = False,
    ) -> None:
        buffer = pipeline.buffer
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

            if last_update <= self._depth_last_update:
                return

            skip_allowed = allow_skip or self._depth_allow_skip

            expected = self._depth_last_update + 1

            if prev_update == self._depth_last_update:
                update = self._build_depth_update(message, event_time)
                self._depth_last_update = last_update
                self._depth_allow_skip = False
            elif skip_allowed and first_update <= expected <= last_update:
                update = self._build_depth_update(message, event_time)
                self._depth_last_update = last_update
                self._depth_allow_skip = False
            else:
                resync_reason = ResyncReason.SEQUENCE_GAP
                if first_update <= expected <= last_update:
                    resync_details = (
                        f"Предыдущий апдейт {prev_update} != {self._depth_last_update}"
                    )
                else:
                    resync_details = (
                        f"Ожидали {expected}, получили диапазон {first_update}-{last_update}"
                    )
                self._depth_allow_skip = False

        if resync_reason is not None:
            self._trigger_depth_resync(
                pipeline,
                reason=resync_reason,
                details=resync_details,
            )
            return

        if update is not None:
            pipeline.push_data(update)

    def _trigger_depth_resync(
        self,
        pipeline: DepthStreamPipeline,
        reason: ResyncReason,
        details: str,
    ) -> None:
        self._logger.log_resync(reason, details)
        pipeline.push_resync(reason, details)
        self._reset_depth_state()
        coordinator = self._resync_coordinator
        if coordinator is not None:
            task = self._build_depth_resync_task(pipeline, reason, details)
            if coordinator.submit(task):
                return
            self._logger.log(
                (
                    "Binance depth stream {symbol}: восстановление уже в обработке"
                ).format(symbol=self.symbol)
            )
            return
        try:
            snapshot = self.fetch_orderbook_snapshot()
        except Exception as exc:  # noqa: BLE001
            message = f"Ошибка получения снапшота: {exc}"
            pipeline.push_resync(ResyncReason.CONNECTION_LOST, details=message)
            self._logger.log_resync(ResyncReason.CONNECTION_LOST, message)
            return
        self._apply_depth_snapshot(snapshot, pipeline)

    def _build_depth_resync_task(
        self,
        pipeline: DepthStreamPipeline,
        reason: ResyncReason,
        details: str,
    ) -> ResyncTask:
        symbol = self.symbol

        def fetch() -> OrderBookSnapshot:
            return fetch_snapshot(
                rest_get=self._rest_get,
                symbol=symbol,
                depth_limit=self._depth_limit,
                level_builder=self._build_level,
            )

        def apply(snapshot: OrderBookSnapshot) -> Sequence[DepthMessage]:
            with self._lock:
                self._depth_last_update = snapshot.last_update_id
                self._depth_allow_skip = True
                pending = tuple(self._depth_buffered_messages)
                self._depth_buffered_messages.clear()
            apply_snapshot(pipeline=pipeline, snapshot=snapshot)
            return pending

        def replay(pending: Sequence[DepthMessage]) -> None:
            replay_delta(
                pending,
                lambda message: self._handle_depth_message(
                    message,
                    pipeline,
                    buffer_if_uninitialized=False,
                    allow_skip=True,
                ),
            )

        def validate() -> bool:
            with self._lock:
                return self._depth_last_update is not None

        def on_failure(exc: Exception) -> None:
            message = (
                f"Binance depth stream {symbol}: не удалось восстановить ({details}): {exc}"
            )
            pipeline.push_resync(ResyncReason.CONNECTION_LOST, details=message)
            self._logger.log_resync(ResyncReason.CONNECTION_LOST, message)
            self._reset_depth_state()

        def on_success() -> None:
            with self._lock:
                update_id = self._depth_last_update
            self._logger.log(
                (
                    "Binance depth stream {symbol}: восстановление завершено, "
                    "ID {update_id}"
                ).format(symbol=symbol, update_id=update_id)
            )

        return ResyncTask(
            symbol=symbol,
            reason=reason,
            fetch_snapshot=fetch,
            apply_snapshot=apply,
            replay_delta=replay,
            validate=validate,
            on_failure=on_failure,
            on_success=on_success,
        )

    def _reset_depth_state(self) -> None:
        with self._lock:
            self._depth_last_update = None
            self._depth_allow_skip = False
            self._depth_buffered_messages.clear()

    def _apply_depth_snapshot(
        self, snapshot: OrderBookSnapshot, pipeline: DepthStreamPipeline
    ) -> None:
        with self._lock:
            self._depth_last_update = snapshot.last_update_id
            self._depth_allow_skip = True
            pending = tuple(self._depth_buffered_messages)
            self._depth_buffered_messages.clear()

        apply_snapshot(pipeline=pipeline, snapshot=snapshot)
        replay_delta(
            pending,
            lambda message: self._handle_depth_message(
                message,
                pipeline,
                buffer_if_uninitialized=False,
                allow_skip=True,
            ),
        )

    def stream_book_ticker(self) -> StreamSubscription[BestBidAsk]:
        silence_timeout = self._book_ticker_silence_timeout
        heartbeat_interval = max(silence_timeout / 2, 0.1)

        buffer: StreamBuffer[BestBidAsk] = StreamBuffer(
            name="book_ticker",
            logger=self._logger,
            silence_timeout=silence_timeout,
            heartbeat_interval=heartbeat_interval,
            maxsize=BookTickerStreamPipeline.buffer_size(self._profile),
            drop_oldest_on_overflow=True,
            restart_grace_period=self._resolve_restart_grace_period("book"),
        )
        pipeline = BookTickerStreamPipeline(
            name="book_ticker",
            buffer=buffer,
            profile=self._profile,
            on_chronic_error=lambda reason, details: self._request_stream_migration(
                "book", reason, details or "book ticker chronic degradation"
            ),
        )
        self._ticker_pipeline = pipeline

        def handle_message(message: dict[str, Any]) -> None:
            for payload in self._parse_book_ticker(message):
                pipeline.push_data(payload)

        def handle_error(reason: ResyncReason, details: str) -> None:
            suffix = f"(symbol {self.symbol})"
            message = details if suffix in details else f"{details} {suffix}"
            should_log = not (
                reason == ResyncReason.CONNECTION_LOST
                and "ошибка чтения сокета" in details
            )
            pipeline.push_resync(reason, message)
            if should_log:
                self._logger.log(message)

        release = self._stream_manager.register_book_ticker(
            self.symbol,
            pipeline.buffer,
            handle_message,
            handle_error,
            weight=self._stream_weight("book"),
        )

        def iterator() -> Iterator[StreamEvent[BestBidAsk]]:
            try:
                while True:
                    yield pipeline.next_event()
            finally:
                pipeline.buffer.stop()
                release()

        return StreamSubscription(events=iterator(), _buffer=pipeline.buffer, _stopper=release)

    def stream_trades(self) -> StreamSubscription[Trade]:
        silence_timeout = self._trade_silence_timeout
        heartbeat_interval = max(silence_timeout / 2, 0.1)

        buffer: StreamBuffer[Trade] = StreamBuffer(
            name="trades",
            logger=self._logger,
            silence_timeout=silence_timeout,
            heartbeat_interval=heartbeat_interval,
            maxsize=TradesStreamPipeline.buffer_size(self._profile),
            drop_oldest_on_overflow=True,
            restart_grace_period=self._resolve_restart_grace_period("trades"),
        )
        pipeline = TradesStreamPipeline(
            name="trades",
            buffer=buffer,
            profile=self._profile,
            on_chronic_error=lambda reason, details: self._request_stream_migration(
                "trades", reason, details or "trade stream chronic degradation"
            ),
        )
        self._trades_pipeline = pipeline

        def handle_message(message: dict[str, Any]) -> None:
            for payload in self._parse_trade(message):
                pipeline.push_data(payload)

        def handle_error(reason: ResyncReason, details: str) -> None:
            suffix = f"(symbol {self.symbol})"
            message = details if suffix in details else f"{details} {suffix}"
            should_log = not (
                reason == ResyncReason.CONNECTION_LOST
                and "ошибка чтения сокета" in details
            )
            pipeline.push_resync(reason, message)
            if should_log:
                self._logger.log(message)

        release = self._stream_manager.register_trades(
            self.symbol,
            pipeline.buffer,
            handle_message,
            handle_error,
            weight=self._stream_weight("trades"),
        )

        def iterator() -> Iterator[StreamEvent[Trade]]:
            try:
                while True:
                    yield pipeline.next_event()
            finally:
                pipeline.buffer.stop()
                release()

        return StreamSubscription(events=iterator(), _buffer=pipeline.buffer, _stopper=release)

    @property
    def depth_pipeline(self) -> Optional[DepthStreamPipeline]:
        return self._depth_pipeline

    @property
    def trades_pipeline(self) -> Optional[TradesStreamPipeline]:
        return self._trades_pipeline

    @property
    def ticker_pipeline(self) -> Optional[BookTickerStreamPipeline]:
        return self._ticker_pipeline

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
        silence_timeout = self._generic_silence_timeout
        heartbeat_interval = max(silence_timeout / 2, 0.1)

        buffer: StreamBuffer[Any] = StreamBuffer(
            name=name,
            logger=self._logger,
            silence_timeout=silence_timeout,
            heartbeat_interval=heartbeat_interval,
            maxsize=maxsize,
            drop_oldest_on_overflow=drop_oldest_on_overflow,
            restart_grace_period=silence_timeout,
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
        pending_restart_reason: ResyncReason | None = None

        def _describe(reason: ResyncReason | None) -> str:
            if reason == ResyncReason.SILENCE_TIMEOUT:
                return "тайм-аута тишины"
            if reason == ResyncReason.CONNECTION_LOST:
                return "обрыва соединения"
            return "неизвестного события"

        def _log(message: str, reason: ResyncReason | None = None) -> None:
            if reason is None:
                self._logger.log(f"Binance {name} stream: {message}")
            else:
                self._logger.log(
                    f"Binance {name} stream: {message} ({_describe(reason)})"
                )

        while not buffer.stopped():
            client: WebSocketClient | None = None
            try:
                if pending_restart_reason is None:
                    self._logger.log(f"Binance {name} stream: открываем соединение")
                else:
                    _log("перезапуск соединения", pending_restart_reason)
                client = self._connect_websocket(url)
                client.settimeout(self._ws_timeout)
                if pending_restart_reason is not None:
                    _log(
                        "соединение успешно восстановлено",
                        pending_restart_reason,
                    )
                    pending_restart_reason = None
                while not buffer.stopped():
                    restart_reason = buffer.consume_restart_request()
                    if restart_reason is not None:
                        pending_restart_reason = restart_reason
                        _log("получен запрос перезапуска от буфера", restart_reason)
                        break
                    try:
                        message = cast(dict[str, Any], client.recv_json())
                    except WebSocketTimeoutError:
                        restart_reason = buffer.consume_restart_request()
                        if restart_reason is not None:
                            pending_restart_reason = restart_reason
                            _log(
                                "перезапуск по запросу буфера после тайм-аута ожидания",
                                restart_reason,
                            )
                            break
                        buffer.push(StreamEvent.heartbeat())
                        continue
                    if not message:
                        restart_reason = buffer.consume_restart_request()
                        if restart_reason is not None:
                            pending_restart_reason = restart_reason
                            _log(
                                "перезапуск по запросу буфера после пустого сообщения",
                                restart_reason,
                            )
                            break
                        continue
                    for payload in parser(message):
                        buffer.push_data(payload)
                    restart_reason = buffer.consume_restart_request()
                    if restart_reason is not None:
                        pending_restart_reason = restart_reason
                        _log(
                            "перезапуск по запросу буфера после обработки сообщения",
                            restart_reason,
                        )
                        break
            except Exception as exc:
                reason = (
                    ResyncReason.SILENCE_TIMEOUT
                    if pending_restart_reason == ResyncReason.SILENCE_TIMEOUT
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
                    details = (
                        f"Binance {name} stream: обнаружен обрыв соединения: {exc}"
                    )
                    buffer.push_resync(reason, details)
                    self._logger.log(details)
                    time.sleep(self._reconnect_delay)
                pending_restart_reason = reason
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass

    def _connect_websocket(
        self, url: str
    ) -> "WebSocketClient":
        return ThreadedWebSocketClient(
            url,
            timeout=self._ws_timeout,
            heartbeat_interval=self._heartbeat_interval,
            heartbeat_timeout=self._ws_timeout,
        )

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


__all__ = ["BinanceExchangeData", "fetch_snapshot", "apply_snapshot", "replay_delta"]
