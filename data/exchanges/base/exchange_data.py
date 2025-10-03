from typing import Iterator, Protocol

from domain.models import Candle, OrderBookSnapshot, SymbolFilters, Trade

from .best_bid_ask import BestBidAsk
from .depth_stream_data import DepthStreamData
from .stream_event import StreamEvent


class IExchangeData(Protocol):
    def fetch_symbol_filters(self) -> SymbolFilters:
        ...

    def fetch_orderbook_snapshot(self) -> OrderBookSnapshot:
        ...

    def stream_depth(self) -> Iterator[StreamEvent[DepthStreamData]]:
        ...

    def stream_book_ticker(self) -> Iterator[StreamEvent[BestBidAsk]]:
        ...

    def stream_trades(self) -> Iterator[StreamEvent[Trade]]:
        ...

    def stream_kline_1m(self) -> Iterator[StreamEvent[Candle]]:
        ...


__all__ = ["IExchangeData"]
