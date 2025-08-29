from zoneinfo import ZoneInfo

from domain.models.Timeframe import Timeframe

# 🌍 Часовой пояс
TIMEZONE: ZoneInfo = ZoneInfo("Europe/Belgrade")

TIMEFRAMES: list[Timeframe] = [Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1]

OUTPUT_PLOT_PATH: str = '.generated/plot'

# region График
# 🎨 Цвета графика
COLOR_UP: str = '#078772'
COLOR_DOWN: str = '#d42f2f'
COLOR_BACKGROUND: str = '#0f0f0f'
COLOR_BACKGROUND_NA: str = '#111111'
SWING_COLOR_HIGH: str = '#ffe0b2'
SWING_COLOR_LOW: str = '#ffa726'

# 📏 Размер стрелок на графике
SWING_MARKER_SIZE: int = 25

# 🕒 Форматирование времени по оси X
X_AXIS_TIME_FORMAT: str = '%H:%M'

# 📉 Ширина свечи (в долях от интервала между свечами)
CANDLESTICK_WIDTH_MULTIPLIER: float = 0.5

# 📐 Размер графика
PLOT_WIDTH_INCHES: float = 12
PLOT_HEIGHT_INCHES: float = 6
PLOT_DPI: int = 100
# endregion

ATR_PERIOD: int = 14
ATR_BREAKOUT_MULTIPLIER: float = 3
# Фильтр значимости пары (l/h)
SWING_FILTER_AMP_ATR_MULTIPLIER: float = 3.5
MIN_SWING_WIDTH_BARS: int = max(6, ATR_PERIOD//2)

TREND_ITERATIONS: int = 3
FRESH_MAX_AGE: int = max(14, round(1.5 * TREND_ITERATIONS))
BARS_LIMIT: int = 1500
WINDOW_TAIL: int = max(BARS_LIMIT, 3 * TREND_ITERATIONS + 100)
MIN_PULLBACK_BARS = ATR_PERIOD
HIGH_SHIFT = 3 * MIN_PULLBACK_BARS

MIN_RR = 1.05

HAS_LOGARITHMIC_SCALE = False

# Максимум баров на поиск первого ретеста после подтверждения
RETEST_MAX_BARS: int = 60
# Допуск к уровню при поиске ретеста (в ATR текущего бара)
RETEST_ATR_TOL_MULT: float = 1
# Сколько баров отводим на подтверждение «реакции» после ретеста
REACTION_CONFIRM_BARS: int = 2