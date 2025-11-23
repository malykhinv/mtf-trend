from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from crypto_screener.domain.models.bar import Bar
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
