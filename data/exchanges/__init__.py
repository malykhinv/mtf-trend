from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from config.timezone import CURRENT_TIMEZONE
from data.logger import LogSink
from domain.models import (
    BalanceSource,
    Exchange,
    ExecutionReport,
    MarginMode,
    Side,
    OrderBookSnapshot,
    OrderBookUpdate,
    StopTrigger,
    SymbolFilters,
    Trade,
)

from .binance import (
    BestBidAsk,
    BinanceExchangeData,
    BinanceStreamManager,
    BinanceSymbolStreams,
    DepthStreamData,
    StreamLimitError,
    StreamSubscription,
)
from .events import ResyncReason, StreamEvent, StreamEventType
from .stream_buffer import StreamBuffer


@dataclass(slots=True)
class ExchangeLogger:
    sink: LogSink

    def log(self, message: str) -> None:
        timestamp = datetime.now(tz=CURRENT_TIMEZONE)
        formatted = f"{timestamp:%H:%M:%S} {message}"
        self.sink(formatted)


class IExchangeData(Protocol):
    def fetch_symbol_filters(self) -> SymbolFilters: ...

    def fetch_orderbook_snapshot(self) -> OrderBookSnapshot: ...

    def fetch_next_funding_time(self) -> Optional[datetime]: ...

    def stream_depth(self) -> StreamSubscription[DepthStreamData]: ...

    def stream_trades(self) -> StreamSubscription[Trade]: ...

    def stream_book_ticker(self) -> StreamSubscription[BestBidAsk]: ...


class IExchangeTrade(Protocol):
    def get_balance(self, source: BalanceSource) -> float: ...

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]: ...

    def place_market(self, side, quantity, *, reason: Optional[str] = None) -> ExecutionReport: ...

    def place_stop_market(
        self,
        side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None: ...


class BinanceTradingAdapter(IExchangeTrade):
    def __init__(
        self,
        *,
        symbol: str,
        quote_asset: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        self._symbol = symbol.upper()
        self._quote_asset = quote_asset
        self._api_key = api_key
        self._api_secret = api_secret

    def get_balance(self, source: BalanceSource) -> float:
        return 0.0

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]:
        return leverage

    def place_market(
        self,
        side: Side,
        quantity: float,
        *,
        reason: Optional[str] = None,
    ) -> ExecutionReport:
        executed_at = datetime.now(tz=CURRENT_TIMEZONE)
        return ExecutionReport(
            exchange=Exchange.BINANCE,
            symbol=self._symbol,
            order_id="SIMULATED",
            side=side,
            price=0.0,
            quantity=quantity,
            executed_qty=quantity,
            status="FILLED",
            commission=0.0,
            executed_at=executed_at,
        )

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None:
        return None


class BybitExchangeData(IExchangeData):
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise NotImplementedError("Bybit data access is not available")

    def fetch_symbol_filters(self) -> SymbolFilters:
        raise NotImplementedError

    def fetch_orderbook_snapshot(self) -> OrderBookSnapshot:
        raise NotImplementedError

    def fetch_next_funding_time(self) -> Optional[datetime]:
        raise NotImplementedError

    def stream_depth(self) -> StreamSubscription[DepthStreamData]:
        raise NotImplementedError

    def stream_trades(self) -> StreamSubscription[Trade]:
        raise NotImplementedError

    def stream_book_ticker(self) -> StreamSubscription[BestBidAsk]:
        raise NotImplementedError


class BybitTradingAdapter(IExchangeTrade):
    def __init__(
        self,
        *,
        symbol: str,
        settle_coin: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        self._symbol = symbol.upper()
        self._settle_coin = settle_coin
        self._api_key = api_key
        self._api_secret = api_secret

    def get_balance(self, source: BalanceSource) -> float:
        return 0.0

    def set_leverage(self, leverage: int, margin_mode: MarginMode) -> Optional[int]:
        return leverage

    def place_market(
        self,
        side: Side,
        quantity: float,
        *,
        reason: Optional[str] = None,
    ) -> ExecutionReport:
        executed_at = datetime.now(tz=CURRENT_TIMEZONE)
        return ExecutionReport(
            exchange=Exchange.BYBIT,
            symbol=self._symbol,
            order_id="SIMULATED",
            side=side,
            price=0.0,
            quantity=quantity,
            executed_qty=quantity,
            status="FILLED",
            commission=0.0,
            executed_at=executed_at,
        )

    def place_stop_market(
        self,
        side: Side,
        stop_price: float,
        quantity: float,
        trigger: StopTrigger,
    ) -> None:
        return None


__all__ = [
    "BestBidAsk",
    "BinanceExchangeData",
    "BinanceStreamManager",
    "BinanceSymbolStreams",
    "BinanceTradingAdapter",
    "StreamLimitError",
    "BybitExchangeData",
    "BybitTradingAdapter",
    "DepthStreamData",
    "ExchangeLogger",
    "IExchangeData",
    "IExchangeTrade",
    "ResyncReason",
    "StreamBuffer",
    "StreamEvent",
    "StreamEventType",
    "StreamSubscription",
]

