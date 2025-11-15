from dataclasses import dataclass
from datetime import datetime

from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class Live:
    pass


@dataclass(frozen=True)
class TestMarket:
    pass


@dataclass(frozen=True)
class TestSymbol:
    symbol: str
    timeframe: Timeframe
    limit: int
    end: datetime


Mode = Live | TestMarket | TestSymbol
