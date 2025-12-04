from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from crypto_screener.domain.models.mode import Live, Mode, TestMarket, PlotPolicy, TestSymbols, \
    TestData
from crypto_screener.domain.models.timeframe import Timeframe

# Временная зона.
_timezone: ZoneInfo = ZoneInfo("Europe/Belgrade")

# Таймфреймы.
_timeframes: list[Timeframe] = [Timeframe.H1, Timeframe.M30, Timeframe.M15, Timeframe.M5]

# Данные для тестирования конкретных символов.
_test_data: list[TestData] = [
    TestData('ICPUSDT', Timeframe.M5, datetime(year=2025, month=11, day=4, hour=3, minute=14)),
    TestData('KAIAUSDT', Timeframe.M5, datetime(year=2025, month=6, day=10, hour=23, minute=54)),
    TestData('MOODENGUSDT', Timeframe.M5, datetime(year=2025, month=5, day=11, hour=12, minute=10)),
    TestData('PNUTUSDT', Timeframe.M15, datetime(year=2025, month=5, day=11, hour=12, minute=46)),
    TestData('WIFUSDT', Timeframe.H1, datetime(year=2024, month=9, day=24, hour=9, minute=1)),
    TestData('ASTERUSDT', Timeframe.M15, datetime(year=2025, month=9, day=23, hour=7, minute=1)),
    TestData('TNSRUSDT', Timeframe.M5, datetime(year=2025, month=11, day=20, hour=6, minute=21)),
    TestData('LSKUSDT', Timeframe.M5, datetime(year=2025, month=11, day=29, hour=14, minute=26)),
    TestData('TRADOORUSDT', Timeframe.M15, datetime(year=2025, month=11, day=16, hour=23, minute=35)),
]

# region Режимы работы.
_mode_live = Live(
    timeframes=_timeframes,
    limit=400,
    listing_period_days=int(365 / 12),
    volume_24h_new_usdt_min=5_000_000,
    volume_24h_old_usdt_min=50_000_000,
    trades_24h_min=500_000,
    trades_24h_btc_ratio_min=0.5
)

_mode_test_market = TestMarket(
    timeframes=_timeframes,
    limit=400,
    window=160,
    history_months=1,
    plot_policy=PlotPolicy.ON_TRADE_SETUP,
    volume_24h_usdt_min=20_000_000,
    trades_24h_min=300_000,
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
    # Временная зона.
    TIMEZONE: ZoneInfo = _timezone

    # Режим работы.
    MODE: Mode = _mode_test_market

    # region Контекст.
    CONTEXT_TRADES_MIN: int = 500_000
    CONTEXT_VOLUME_MIN: float = 500_000_000
    # endregion

    # region Капитализация.
    CAPITALIZATION_LOW_MIN: float = 20_000_000
    CAPITALIZATION_MIDDLE_MIN: float = 1_000_000_000
    CAPITALIZATION_HIGH_MIN: float = 10_000_000_000
    # endregion

    # region Повышенный объем.
    # Минимальная длина окна для нахождения зоны повышенного объема.
    VOLUME_TRIM_SIDE_BARS_MIN: int = 10
    # Порог изменения объема для нахождения зоны повышенного объема.
    HIGH_VOLUME_THRESHOLD: int = 5
    # Порог свечей с повышенным объемом в зоне повышенного объема.
    HIGH_VOLUME_FRACTION_MIN: float = 0.5
    # endregion

    # region Движение цены.
    # Максимальная доля теней относительно общего диапазона свечей.
    SHADOW_RANGE_PCT_MAX: float = 70.0
    # Максимальная доля свечей в предпиковом окне, находящихся выше цен отката.
    PRE_LOW_ABOVE_CORRECTION_LOW_FRACTION_MAX: float = 0.05
    # Максимальное соотношение длительности роста к длительности коррекции.
    RISE_AGE_LIMIT_MULTIPLIER: float = 2.5
    # Рост цены на основных свингах в зоне повышенного объема.
    PRICE_RISE_PCT_MIN: float = 15.0
    # Максимально допустимый откат на участке роста (относительно размера роста)
    MAX_RETRACE_RATIO: float = 0.7
    # Соотношение глубины отката к росту.
    RETRACE_RATIO_MAX: float = 0.75
    # Минимальный уровень отката (снизу-вверх), на котором может располагаться каскад.
    CASCADE_RETRACE_RATIO_MIN: float = 0.4
    # Максимальный разброс цены в каскаде относительно размера отката.
    CASCADE_RANGE_RATIO_MAX: float = 0.2
    # Размер окна ATR для анализа лонгового каскада.
    CASCADE_ATR_WINDOW: int = 50
    # Допуск по касаниям в долях ATR.
    CASCADE_TOUCH_EPS_NATR: float = 1.0
    # Минимальная глубина отката в долях ATR.
    CASCADE_MIN_PULLBACK_NATR: float = 2.5
    # Минимальная длительность отката в барах.
    CASCADE_MIN_PULLBACK_BARS: int = 3
    # Минимальный разрыв между касаниями в барах.
    CASCADE_MIN_GAP_BARS: int = 5
    # Минимальное число свингов для образования каскада.
    CASCADE_LENGTH_MIN: int = 3
    # Минимальный разрыв от верха каскада до начала сопротивления (в долях отката).
    RESISTANCE_GAP_RATIO_MIN: float = 0.1
    # Максимальное количество свингов после каскада.
    RESISTANCE_COUNT_MAX: int = 2
    # Минимальное соотношение отступа поддержки от низа проторговки к размеру проторговки.
    SUPPORT_CONSOLIDATION_RATIO_MIN: float = 0.3
    # endregion

    # region Позиции.
    # Минимальное соотношение Entry-TP к Entry-SL.
    REWARD_RISK_RATIO_MIN: float = 1 / 1
    # Минимальное расстояние Entry-TP.
    PROFIT_PCT_MIN: float = 3
    # Минимальное расстояние Entry-SL.
    LOSS_PCT_MIN: float = 0.3
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
    PLOT_HEIGHT_INCHES: float = 12
    PLOT_DPI: int = 110
    # Цвета графика.
    PLOT_TICK_COLOR: str = "white"
    PLOT_TITLE_COLOR: str = "white"
    PLOT_COLOR_UP: str = "#078772"
    PLOT_COLOR_DOWN: str = "#d42f2f"
    PLOT_BACKGROUND_COLOR: str = "#0f0f0f"
    PLOT_GRID_COLOR: str = "#2f2f2f"
    PLOT_VOLUME_COLOR: str = "#2f2f2f"
    PLOT_MAIN_HIGH_SWING_COLOR: str = "#ffe082"
    PLOT_CASCADE_SWING_COLOR: str = "#64b5f6"
    PLOT_CASCADE_LEVEL_COLOR: str = "#64b5f6"
    PLOT_CASCADE_LEVEL_LINEWIDTH: float = 1.6
    PLOT_CASCADE_LEVEL_LINESTYLE: str = "--"
    PLOT_CASCADE_LEVEL_ALPHA: float = 0.7
    PLOT_CASCADE_LEVEL_ZORDER: int = 1
    PLOT_RESISTANCE_SWING_COLOR: str = "#ef5350"
    PLOT_SUPPORT_SWING_COLOR: str = "#66bb6a"
    PLOT_COMMON_SWING_COLOR: str = "#663366"
    PLOT_GROWTH_PHASE_COLOR: str = "#808080"
    PLOT_GROWTH_PHASE_ALPHA: float = 0.05
    PLOT_ENTRY_SL_COLOR: str = "#8b1a1a"
    PLOT_ENTRY_TP_COLOR: str = "#0b3b2e"
    PLOT_ENTRY_PC_COLOR: str = "#1c54b2"
    PLOT_ENTRY_BE_COLOR: str = "#ffb74d"
    PLOT_ENTRY_RISK_ZONE_ALPHA: float = 0.14
    PLOT_ENTRY_REWARD_ZONE_ALPHA: float = 0.12
    PLOT_ENTRY_ZONE_ALPHA: float = 0.12
    PLOT_ENTRY_ZONE_MIN_HEIGHT: float = 1e-5
    PLOT_ENTRY_ZONE_ZORDER: int = 0
    # Маркеры свингов.
    PLOT_SWING_MARKER_SIZE: int = 32
    PLOT_SWING_MARKER_CLOSED_ALPHA: float = 0.25
    PLOT_SWING_MARKER_OPEN_ALPHA: float = 1.0
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
    PLOT_VOLUME_ALPHA: float = 0.5
    PLOT_VOLUME_PAD_RATIO: float = 0.05
    PLOT_VOLUME_ZORDER: int = 1
    PLOT_HEIGHT_RATIOS: tuple[int, int] = (3, 1)
    PLOT_SUBPLOT_HSPACE: float = 0.03
    PLOT_PRICE_DECIMALS_HIGH: int = 2
    PLOT_PRICE_DECIMALS_MID: int = 4
    PLOT_PRICE_DECIMALS_LOW: int = 6
    PLOT_PRICE_HIGH_THRESHOLD: float = 100
    PLOT_PRICE_MID_THRESHOLD: float = 1
    PLOT_DEFAULT_SYMBOL: str = "asset"
    PLOT_POSTMORTEM_EXTRA_BARS: int = 5
    # endregion

    # region Тест
    TEST_SLIPPAGE_PCT: float = 0.3
    # endregion


cfg = AppConfig()
