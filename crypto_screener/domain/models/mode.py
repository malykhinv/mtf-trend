from dataclasses import dataclass
from datetime import datetime

from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class Live:
    timeframes: list[Timeframe]
    limit: int
    listing_period_days: int
    volume_24h_new_usdt_min: int
    volume_24h_old_usdt_min: int
    trades_24h_min: int
    trades_24h_btc_ratio_min: float


@dataclass(frozen=True)
class TestMarket:
    timeframes: list[Timeframe]
    limit: int


@dataclass(frozen=True)
class TestSymbol:
    symbol: str
    timeframe: Timeframe
    limit: int
    end: datetime


Mode = Live | TestMarket | TestSymbol
