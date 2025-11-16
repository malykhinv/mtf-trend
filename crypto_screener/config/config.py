from dataclasses import dataclass
from zoneinfo import ZoneInfo

from crypto_screener.domain.models.mode import Live, Mode
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class AppConfig:
    # Режим работы.
    MODE: Mode = Live(
        timeframes=[Timeframe.H1, Timeframe.M30, Timeframe.M15, Timeframe.M5],
        limit=1000,
        listing_period_days=14,
        volume_24h_new_usdt_min=5_000_000,
        volume_24h_old_usdt_min=50_000_000,
        trades_24h_min=1_000_000,
        trades_24h_btc_ratio_min=0.5,
    )
    # Временная зона.
    TIMEZONE: ZoneInfo = ZoneInfo("Europe/Belgrade")
    # Порог изменения объема для нахождения зоны повышенного объема.
    HIGH_VOLUME_THRESHOLD: int = 5
    # Порог свечей с повышенным объемом в зоне повышенного объема.
    HIGH_VOLUME_FRACTION_MIN: float = 0.5
    # Рост цены на основных свингах в зоне повышенного объема.
    PRICE_RISE_PCT_MIN: float = 6.0
    # Соотношение глубины отката к росту.
    RETRACE_RATIO_MAX: float = 0.5
    # Минимальный уровень отката (снизу-вверх), на котором может располагаться лонговый каскад.
    CASCADE_LONG_RETRACE_RATIO_MIN: float = 0.5
    # Максимальный разброс цены в каскаде относительно размера отката.
    CASCADE_RANGE_RATIO_MAX: float = 0.2
    # Минимальное число свингов для образования каскада.
    CASCADE_LENGTH_MIN: int = 3
    # Максимальное количество свингов после каскада.
    RESISTANCE_COUNT_MAX: int = 1


cfg = AppConfig()
