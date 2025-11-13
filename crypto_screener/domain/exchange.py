from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class FuturesSymbol:
    symbol: str
    listing_ts: datetime
    volume_usdt_24h: float
    trades_24h: int


class Exchange(ABC):
    @abstractmethod
    def get_futures_symbols(self) -> list[FuturesSymbol]:
        ...

    @abstractmethod
    def get_ohlcv(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Bar]:
        ...
