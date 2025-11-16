from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from crypto_screener.domain.models.mode import Live, Mode, TestMarket, TestSymbol
from crypto_screener.domain.models.timeframe import Timeframe

# Временная зона.
_timezone: ZoneInfo = ZoneInfo("Europe/Belgrade")

# region Режимы работы.
_mode_live = Live(
    timeframes=[Timeframe.H1, Timeframe.M30, Timeframe.M15, Timeframe.M5],
    limit=1000,
    listing_period_days=14,
    volume_24h_new_usdt_min=5_000_000,
    volume_24h_old_usdt_min=50_000_000,
    trades_24h_min=1_000_000,
    trades_24h_btc_ratio_min=0.5
)

_mode_test_market = TestMarket(
    timeframes=[Timeframe.H1, Timeframe.M30, Timeframe.M15, Timeframe.M5],
    limit=1000
)

_mode_test_symbol = TestSymbol(
    symbol='BTCUSDT',
    timeframe=Timeframe.H1,
    limit=1000,
    end=datetime(
        year=2025,
        month=11,
        day=16,
        hour=16,
        minute=32,
        tzinfo=_timezone
    )
)


# endregion

@dataclass(frozen=True)
class AppConfig:
    # Временная зона.
    TIMEZONE: ZoneInfo = _timezone

    # Режим работы.
    MODE: Mode = _mode_live

    # region Объем.
    # Минимальная длина окна для нахождения зоны повышенного объема.
    VOLUME_TRIM_SIDE_BARS_MIN: int = 10
    # Порог изменения объема для нахождения зоны повышенного объема.
    HIGH_VOLUME_THRESHOLD: int = 5
    # Порог свечей с повышенным объемом в зоне повышенного объема.
    HIGH_VOLUME_FRACTION_MIN: float = 0.5
    # endregion

    # region Движение цены.
    # Рост цены на основных свингах в зоне повышенного объема.
    PRICE_RISE_PCT_MIN: float = 6.0
    # Соотношение глубины отката к росту.
    RETRACE_RATIO_MAX: float = 0.5
    # Минимальный уровень отката (снизу-вверх), на котором может располагаться каскад.
    CASCADE_RETRACE_RATIO_MIN: float = 0.5
    # Максимальный разброс цены в каскаде относительно размера отката.
    CASCADE_RANGE_RATIO_MAX: float = 0.2
    # Минимальное число свингов для образования каскада.
    CASCADE_LENGTH_MIN: int = 3
    # Максимальное количество свингов после каскада.
    RESISTANCE_COUNT_MAX: int = 1
    # Минимальное соотношение отступа поддержки от низа проторговки к размеру проторговки.
    SUPPORT_CONSOLIDATION_RATIO_MIN: float = 0.5
    # endregion

    # region Частичное закрытие позиции.
    # Минимальное расстояние от Entry и TP до PC при частичном закрытии позиции.
    PARTIAL_CLOSE_SIDE_PCT_MIN: float = 2.0
    # Соотношение цены участков Entry-BE и Entry-PC при частичном закрытии позиции.
    BREAKEVEN_PARTIAL_CLOSE_RATIO: float = 0.5
    # endregion

    # region Построение графиков.
    # Папка для графиков.
    PLOT_OUTPUT_DIR: str = ".generated/plot"
    # Размер графика.
    PLOT_WIDTH_INCHES: float = 12
    PLOT_HEIGHT_INCHES: float = 6
    PLOT_DPI: int = 110
    # Цвета графика.
    PLOT_TICK_COLOR: str = "white"
    PLOT_TITLE_COLOR: str = "white"
    PLOT_COLOR_UP: str = "#078772"
    PLOT_COLOR_DOWN: str = "#d42f2f"
    PLOT_BACKGROUND_COLOR: str = "#0f0f0f"
    PLOT_GRID_COLOR: str = "#2f2f2f"
    PLOT_MAIN_HIGH_SWING_COLOR: str = "#ffe082"
    PLOT_CASCADE_SWING_COLOR: str = "#64b5f6"
    PLOT_RESISTANCE_SWING_COLOR: str = "#ef5350"
    PLOT_SUPPORT_SWING_COLOR: str = "#66bb6a"
    # Маркеры свингов.
    PLOT_SWING_MARKER_SIZE: int = 35
    PLOT_SWING_MARKER_EDGE_LINEWIDTH: float = 0.6
    PLOT_SWING_MARKER_EDGE_COLOR: str = "white"
    PLOT_SWING_MARKER_ALPHA: float = 0.85
    PLOT_SWING_MARKER_OPEN_ALPHA: float = 0.55
    PLOT_SWING_ZORDER: int = 3
    # Размер и положение свечей.
    PLOT_CANDLE_WIDTH_MULTIPLIER: float = 0.6
    PLOT_CANDLE_WICK_LINEWIDTH: float = 1.1
    PLOT_CANDLE_BODY_MIN_HEIGHT: float = 1e-5
    PLOT_CANDLE_BODY_X_OFFSET_RATIO: float = 0.5
    PLOT_CANDLE_WICK_ZORDER: int = 1
    PLOT_CANDLE_BODY_ZORDER: int = 2
    PLOT_CANDLE_FALLBACK_MIN_TIMES: int = 2
    PLOT_CANDLE_FALLBACK_INTERVAL_MINUTES: int = 1
    PLOT_MINUTES_IN_DAY: int = 24 * 60
    # Форматирование осей.
    PLOT_X_AXIS_TIME_FORMAT: str = "%d %b %H:%M"
    PLOT_X_AXIS_MINTICKS: int = 4
    PLOT_X_AXIS_MAXTICKS: int = 8
    PLOT_X_AXIS_LABEL_ROTATION: int = 0
    PLOT_TICK_LABELSIZE: int = 9
    PLOT_PRICE_PAD_RATIO: float = 0.05
    PLOT_PRICE_PAD_MIN: float = 1e-3
    PLOT_TITLE_PAD: int = 12
    PLOT_GRID_LINEWIDTH: float = 0.6
    PLOT_GRID_ALPHA: float = 0.4
    PLOT_Y_OFFSET_RATIO: float = 0.015
    PLOT_Y_OFFSET_MIN: float = 1e-4
    PLOT_PRICE_DECIMALS_HIGH: int = 2
    PLOT_PRICE_DECIMALS_MID: int = 4
    PLOT_PRICE_DECIMALS_LOW: int = 6
    PLOT_PRICE_HIGH_THRESHOLD: float = 100
    PLOT_PRICE_MID_THRESHOLD: float = 1
    PLOT_DEFAULT_SYMBOL: str = "asset"
    # endregion


cfg = AppConfig()
