from dataclasses import dataclass, field
from typing import List
from zoneinfo import ZoneInfo

from crypto_screener.domain.models.mode import Mode
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class AppConfig:
    MODE: Mode = Mode.LIVE

    TIMEZONE: ZoneInfo = ZoneInfo("Europe/Belgrade")

    TFS: List[Timeframe] = field(default_factory=lambda: [Timeframe.H1, Timeframe.M30, Timeframe.M15, Timeframe.M5])

    NOTIFY_ENABLED: bool = True

    OHLCV_LIMIT: int = 1000
    LISTING_PERIOD_DAYS: int = 14

    VOLUME_24H_NEW_MIN: float = 5_000_000
    VOLUME_24H_OLD_MIN: float = 50_000_000
    TRADES_24H_MIN: int = 1_000_000
    TRADES_24H_BTC_RATIO: float = 0.5


cfg = AppConfig()
