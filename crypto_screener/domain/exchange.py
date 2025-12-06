from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.margin_mode import MarginMode
from crypto_screener.domain.models.order_info import OrderInfo
from crypto_screener.domain.models.order_side import OrderSide
from crypto_screener.domain.models.position import Position
from crypto_screener.domain.models.symbol import FuturesSymbol
from crypto_screener.domain.models.timeframe import Timeframe


class Exchange(ABC):
    @abstractmethod
    def get_futures_symbols(self) -> list[FuturesSymbol]:
        ...

    @abstractmethod
    def get_ohlcv(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int,
            end: Optional[datetime] = None
    ) -> list[Bar]:
        ...

    @abstractmethod
    def place_market_order(
            self,
            symbol: str,
            side: OrderSide,
            quantity: float,
            margin_mode: Optional[MarginMode] = None,
    ) -> str:
        ...

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> None:
        ...

    @abstractmethod
    def get_order_status(self, symbol: str, order_id: str) -> OrderInfo:
        ...

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]:
        ...

    @abstractmethod
    def place_stop_loss_order(
            self,
            symbol: str,
            side: OrderSide,
            quantity: float,
            stop_price: float,
            reduce_only: bool = True,
            margin_mode: Optional[MarginMode] = None,
    ) -> str:
        ...

    @abstractmethod
    def place_take_profit_order(
            self,
            symbol: str,
            side: OrderSide,
            quantity: float,
            price: float,
            reduce_only: bool = True,
            margin_mode: Optional[MarginMode] = None,
    ) -> str:
        ...
