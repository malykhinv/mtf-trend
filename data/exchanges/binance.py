from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Deque, Dict, Generic, Iterator, Mapping, Optional, Tuple, TypeVar

from urllib.error import URLError
from urllib.request import urlopen

from config.config import CONFIG
from config.timezone import CURRENT_TIMEZONE
from data.logger import LogSink
from domain.models import Exchange, OrderBookSnapshot, OrderBookUpdate, SymbolFilters, Trade
from config.models.trading_profile import TradingProfile

from .events import StreamEvent, StreamEventType
from .limits import StreamLimits, load_stream_limits
from .stream_buffer import StreamBuffer


@dataclass(frozen=True, slots=True)
class BestBidAsk:
    bid: float
    ask: float
    timestamp: datetime


DepthStreamData = OrderBookUpdate | OrderBookSnapshot

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
    ) -> None:
        self._symbol = symbol.upper()
        self._loop_interval = max(loop_interval_ms, 100)
        self._silence_timeout = max(silence_timeout_ms, 1_000)
        self._log_writer = log_writer
        self._api_key = api_key
        self._api_secret = api_secret
        self._limits: StreamLimits = load_stream_limits()
        self._exchange_info: Dict[str, Dict[str, object]] = {}
        self._load_exchange_info()

    # ------------------------------------------------------------------
    # Interface implementation expected by the application
    def fetch_symbol_filters(self) -> SymbolFilters:
        info = self._exchange_info.get(self._symbol)
        base_asset = self._symbol.replace("USDT", "")
        quote_asset = "USDT"
        price_tick = 0.1
        qty_step = 0.001
        min_notional = 5.0
        if info is not None:
            base_asset = str(info.get("baseAsset", base_asset))
            quote_asset = str(info.get("quoteAsset", quote_asset))
            price_tick = float(info.get("tickSize", price_tick))
            qty_step = float(info.get("stepSize", qty_step))
            min_notional = float(info.get("notional", min_notional))
        return SymbolFilters(
            exchange=Exchange.BINANCE,
            symbol=self._symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price_tick_size=price_tick,
            quantity_step_size=qty_step,
            min_price=0.0,
            max_price=math.inf,
            min_qty=qty_step,
            max_qty=math.inf,
            min_notional=min_notional,
        )

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

        def generator() -> Iterator[StreamEvent[DepthStreamData]]:
            snapshot = self.fetch_orderbook_snapshot()
            yield StreamEvent(StreamEventType.SNAPSHOT, snapshot, snapshot.received_at)
            interval = self._loop_interval / 1000.0
            while True:
                pending = buffer.drain_pending()
                for event in pending:
                    yield event
                time.sleep(interval)
                yield StreamEvent(StreamEventType.DATA, None, datetime.now(tz=CURRENT_TIMEZONE))

        return StreamSubscription(generator(), buffer)

    def stream_trades(self) -> StreamSubscription[Trade]:
        buffer: StreamBuffer[Trade] = StreamBuffer()

        def generator() -> Iterator[StreamEvent[Trade]]:
            interval = self._loop_interval / 1000.0
            while True:
                pending = buffer.drain_pending()
                for event in pending:
                    yield event
                time.sleep(interval)
                yield StreamEvent(StreamEventType.DATA, None, datetime.now(tz=CURRENT_TIMEZONE))

        return StreamSubscription(generator(), buffer)

    def stream_book_ticker(self) -> StreamSubscription[BestBidAsk]:
        buffer: StreamBuffer[BestBidAsk] = StreamBuffer()

        def generator() -> Iterator[StreamEvent[BestBidAsk]]:
            interval = self._loop_interval / 1000.0
            while True:
                pending = buffer.drain_pending()
                for event in pending:
                    yield event
                time.sleep(interval)
                best = BestBidAsk(0.0, 0.0, datetime.now(tz=CURRENT_TIMEZONE))
                yield StreamEvent(StreamEventType.DATA, best, best.timestamp)

        return StreamSubscription(generator(), buffer)

    # ------------------------------------------------------------------
    def _load_exchange_info(self) -> None:
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        try:
            with urlopen(url, timeout=5) as response:  # noqa: S310
                payload = json.load(response)
        except (URLError, TimeoutError, ValueError, OSError):
            self._exchange_info = {}
            return
        info: Dict[str, Dict[str, object]] = {}
        for entry in payload.get("symbols", []):
            if entry.get("status") != "TRADING":
                continue
            name = str(entry.get("symbol"))
            filters = entry.get("filters", [])
            price_filter = next((f for f in filters if f.get("filterType") == "PRICE_FILTER"), {})
            lot_filter = next((f for f in filters if f.get("filterType") == "LOT_SIZE"), {})
            notional_filter = next(
                (f for f in filters if f.get("filterType") in {"MIN_NOTIONAL", "MARKET_LOT_SIZE"}),
                {},
            )
            info[name] = {
                "baseAsset": entry.get("baseAsset", ""),
                "quoteAsset": entry.get("quoteAsset", ""),
                "tickSize": price_filter.get("tickSize", 0.1),
                "stepSize": lot_filter.get("stepSize", 0.001),
                "notional": notional_filter.get("minNotional", notional_filter.get("minQty", 5.0)),
            }
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
            self._profiles[symbol.upper()] = profile

    def update_weights(self, weights: Mapping[str, float]) -> None:
        for symbol, weight in weights.items():
            self._weights[symbol.upper()] = float(weight)

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
            )
            self._exchange_data[symbol] = exchange_data
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
            )
            self._exchange_data[symbol] = exchange_data
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


