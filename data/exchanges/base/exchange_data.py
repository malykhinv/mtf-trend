from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Thread
from typing import Callable, Generic, Iterator, Optional, Protocol, TypeVar

from domain.models import Candle, OrderBookSnapshot, SymbolFilters, Trade
from .best_bid_ask import BestBidAsk
from .depth_stream_data import DepthStreamData
from .stream_buffer import StreamBuffer
from .stream_event import StreamEvent

T = TypeVar("T")


@dataclass(slots=True)
class StreamSubscription(Generic[T]):
    events: Iterator[StreamEvent[T]]
    _buffer: StreamBuffer[T]
    _stopper: Optional[Callable[[], None]] = None
    _worker: Optional[Thread] = None

    def stop(self) -> None:
        self._buffer.stop()
        if self._stopper is not None:
            try:
                self._stopper()
            except Exception:
                pass
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=1.0)


class IExchangeData(Protocol):
    def fetch_symbol_filters(self) -> SymbolFilters:
        ...

    def fetch_orderbook_snapshot(self) -> OrderBookSnapshot:
        ...

    def stream_depth(self) -> StreamSubscription[DepthStreamData]:
        ...

    def stream_book_ticker(self) -> StreamSubscription[BestBidAsk]:
        ...

    def stream_trades(self) -> StreamSubscription[Trade]:
        ...

    def stream_kline_1m(self) -> StreamSubscription[Candle]:
        ...

    def fetch_next_funding_time(self) -> Optional[datetime]:
        ...


__all__ = ["IExchangeData", "StreamSubscription"]
