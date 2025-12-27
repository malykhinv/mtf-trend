from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Mapping
from zoneinfo import ZoneInfo

from crypto_screener.config.strategy_registry import DEFAULT_STRATEGIES, DEFAULT_STRATEGY_CONFIGS
from crypto_screener.domain.models.mode import Mode, PlotPolicy, TestSymbols, TestData, Live, TestMarket
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.strategies.strategy import Strategy

# Временная зона.
_timezone: ZoneInfo = ZoneInfo("Europe/Belgrade")

# Таймфреймы.
_timeframes: list[Timeframe] = [
    Timeframe.D1,
    # Timeframe.H1,
    # Timeframe.M30,
    # Timeframe.M15,
    # Timeframe.M5,
]
_timeframe_intervals: dict[Timeframe, timedelta] = {
    timeframe: timedelta(minutes=timeframe.minutes)
    for timeframe in Timeframe
}

# Интервалы опроса для анализа таймфреймов.
_poll_intervals: dict[Timeframe, timedelta] = {
    Timeframe.M1: timedelta(seconds=30),
    Timeframe.M5: timedelta(minutes=2),
    Timeframe.M15: timedelta(minutes=5),
    Timeframe.M30: timedelta(minutes=10),
    Timeframe.H1: timedelta(minutes=15),
    Timeframe.H4: timedelta(hours=1),
    Timeframe.D1: timedelta(hours=6),
}

# Интервалы опроса Capture для анализа таймфреймов.
_capture_poll_intervals: dict[Timeframe, timedelta] = {
    Timeframe.M1: timedelta(seconds=1),
    Timeframe.M5: timedelta(seconds=1),
    Timeframe.M15: timedelta(seconds=10),
    Timeframe.M30: timedelta(seconds=20),
    Timeframe.H1: timedelta(minutes=1),
    Timeframe.H4: timedelta(minutes=5),
    Timeframe.D1: timedelta(minutes=30),
}

# Данные для тестирования конкретных символов.
_test_data: list[TestData] = [
    # ППО.
    # TestData('ICPUSDT', Timeframe.M5, datetime(year=2025, month=11, day=4, hour=3, minute=14)),
    # TestData('KAIAUSDT', Timeframe.M5, datetime(year=2025, month=6, day=10, hour=23, minute=54)),
    # TestData('MOODENGUSDT', Timeframe.M5, datetime(year=2025, month=5, day=11, hour=12, minute=10)),
    # TestData('PNUTUSDT', Timeframe.M15, datetime(year=2025, month=5, day=11, hour=12, minute=46)),
    # TestData('WIFUSDT', Timeframe.H1, datetime(year=2024, month=9, day=24, hour=9, minute=1)),
    # TestData('ASTERUSDT', Timeframe.M15, datetime(year=2025, month=9, day=23, hour=7, minute=1)),
    # TestData('TNSRUSDT', Timeframe.M5, datetime(year=2025, month=11, day=20, hour=6, minute=21)),
    # TestData('LSKUSDT', Timeframe.M5, datetime(year=2025, month=11, day=29, hour=14, minute=26)),
    # TestData('TRADOORUSDT', Timeframe.M15, datetime(year=2025, month=11, day=16, hour=23, minute=35)),
    # TestData('MONUSDT', Timeframe.M5, datetime(year=2025, month=12, day=2, hour=10, minute=55)),
    # ГУ.
    TestData('PIPPINUSDT', Timeframe.D1, datetime(year=2025, month=12, day=15, hour=12, minute=0)),
]

# Биржа по умолчанию.
_exchange: str = "Binance"
# _exchange: str = "Bybit"

_live_thresholds = {
    "Binance": dict(
        volume_24h_new_usdt_min=5_000_000,
        volume_24h_old_usdt_min=50_000_000,
        trades_24h_min=500_000,
        trades_24h_btc_ratio_min=0.5,
    ),
    "Bybit": dict(
        volume_24h_new_usdt_min=2_000_000,
        volume_24h_old_usdt_min=20_000_000,
        trades_24h_min=150_000,
        trades_24h_btc_ratio_min=0.35,
    ),
}

_live_cfg = _live_thresholds.get(_exchange, _live_thresholds["Binance"])

# region Режимы работы.
_mode_live = Live(
    timeframes=_timeframes,
    limit=400,
    listing_period_days=int(365 / 12),
    volume_24h_new_usdt_min=_live_cfg["volume_24h_new_usdt_min"],
    volume_24h_old_usdt_min=_live_cfg["volume_24h_old_usdt_min"],
    trades_24h_min=_live_cfg["trades_24h_min"],
    trades_24h_btc_ratio_min=_live_cfg["trades_24h_btc_ratio_min"],
)

_mode_test_market = TestMarket(
    timeframes=_timeframes,
    limit=400,
    window=400,
    history_days=30,
    plot_policy=PlotPolicy.ON_FILLED_SETUP,
    volume_24h_usdt_min=10_000_000,
    trades_24h_min=200_000,
    listing_age_days_min=3
)

_mode_test_symbols = TestSymbols(
    test_data=_test_data,
    limit=400,
    plot_policy=PlotPolicy.ON_ANY
)


# endregion


@dataclass(frozen=True)
class AppConfig:
    # Биржа.
    EXCHANGE: str = _exchange

    # Временная зона.
    TIMEZONE: ZoneInfo = _timezone

    # Интервалы таймфреймов.
    TIMEFRAME_INTERVALS: dict[Timeframe, timedelta] = field(default_factory=lambda: _timeframe_intervals.copy())

    # Интервалы опроса.
    POLL_INTERVALS: dict[Timeframe, timedelta] = field(default_factory=lambda: _poll_intervals.copy())

    # Интервалы опроса для Capture.
    CAPTURE_POLL_INTERVALS: dict[Timeframe, timedelta] = field(default_factory=lambda: _capture_poll_intervals.copy())

    # Режим работы.
    MODE: Mode = _mode_test_market

    # Пул потоков.
    LIVE_MAX_WORKERS: int = 1

    # Стратегии.
    STRATEGIES: list[Strategy] = field(default_factory=lambda: list(DEFAULT_STRATEGIES))

    # Конфигурации стратегий.
    STRATEGY_CONFIGS: Mapping[str, object] = field(default_factory=lambda: DEFAULT_STRATEGY_CONFIGS.copy())


app_cfg = AppConfig()
