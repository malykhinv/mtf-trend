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
    PLOT_COLOR_UP: str = "#078772"
    PLOT_COLOR_DOWN: str = "#d42f2f"
    PLOT_BACKGROUND_COLOR: str = "#0f0f0f"
    PLOT_GRID_COLOR: str = "#2f2f2f"
    PLOT_MAIN_HIGH_SWING_COLOR: str = "#ffe082"
    PLOT_CASCADE_SWING_COLOR: str = "#64b5f6"
    PLOT_RESISTANCE_SWING_COLOR: str = "#ef5350"
    PLOT_SUPPORT_SWING_COLOR: str = "#66bb6a"
    # Размеры маркеров свингов.
    PLOT_SWING_MARKER_SIZE: int = 35
    # Ширина свечей.
    PLOT_CANDLE_WIDTH_MULTIPLIER: float = 0.6
    # Форматирование осей.
    PLOT_X_AXIS_TIME_FORMAT: str = "%d %b %H:%M"
    PLOT_PRICE_DECIMALS_HIGH: int = 2
    PLOT_PRICE_DECIMALS_MID: int = 4
    PLOT_PRICE_DECIMALS_LOW: int = 6
    # endregion


cfg = AppConfig()
