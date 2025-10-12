from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Generic, Iterator, Optional, TypeVar

from urllib.error import URLError
from urllib.request import urlopen

from config.timezone import CURRENT_TIMEZONE
from data.logger import LogSink
from domain.models import Exchange, OrderBookSnapshot, OrderBookUpdate, SymbolFilters, Trade

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


class BinanceExchangeData:
    """A pragmatic placeholder implementation of the data interface."""

    PROFILE_WEIGHTS: Dict[str, float] = {
        "TOP": 1.0,
        "LISTING": 0.8,
        "ALT": 0.5,
        "AUTO": 0.3,
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


