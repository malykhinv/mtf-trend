from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from crypto_screener.domain.models.timeframe import Timeframe


class PlotPolicy(Enum):
    ON_ANY = "ON_ANY"
    ON_SETUP = "ON_SETUP"


# region Mode
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
    window: int
    plot_policy: PlotPolicy


@dataclass(frozen=True)
class TestSymbol:
    symbol: str
    timeframe: Timeframe
    limit: int
    end: datetime
    plot_policy: PlotPolicy


Mode = Live | TestMarket | TestSymbol
# endregion
