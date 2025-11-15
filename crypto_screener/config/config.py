from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from crypto_screener.domain.models.mode import Live, Mode
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class AppConfig:
    MODE: Mode = field(default_factory=Live)

    TIMEZONE: ZoneInfo = ZoneInfo("Europe/Belgrade")

    TFS: list[Timeframe] = field(default_factory=lambda: [Timeframe.H1, Timeframe.M30, Timeframe.M15, Timeframe.M5])

    IS_NOTIFIER_ENABLED: bool = True

    OHLCV_LIMIT: int = 1000
    LISTING_PERIOD_DAYS: int = 14

    VOLUME_24H_NEW_MIN: float = 5_000_000
    VOLUME_24H_OLD_MIN: float = 50_000_000
    TRADES_24H_MIN: int = 1_000_000
    TRADES_24H_BTC_RATIO: float = 0.5

    HIGH_VOLUME_THRESHOLD: int = 5
    HIGH_VOLUME_FRACTION_MIN: float = 0.5
    CASCADE_LENGTH_MIN: int = 3
    RESISTANCE_COUNT_MAX: int = 1


cfg = AppConfig()
