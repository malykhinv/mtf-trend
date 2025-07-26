# region Общие параметры торговли
from zoneinfo import ZoneInfo

from domain.models.mtf_profile import MTFProfile
from domain.models.timeframe import Timeframe

# Timezone
TIMEZONE: ZoneInfo = ZoneInfo("Europe/Belgrade")

# Multi-timeframe profiles
MTF_PROFILE_MACRO_1: MTFProfile = MTFProfile(macro=Timeframe.D1, context=Timeframe.M30, setup=Timeframe.M1)
MTF_PROFILE_MACRO_3: MTFProfile = MTFProfile(macro=Timeframe.D1, context=Timeframe.H1, setup=Timeframe.M3)
MTF_PROFILE_MACRO_5: MTFProfile = MTFProfile(macro=Timeframe.D1, context=Timeframe.H1, setup=Timeframe.M5)

FLOAT_UNDEFINED: float = 0.0

MIN_BARS_BETWEEN_SWINGS: int = 3
MAX_RANGE_PERCENT: int = 100
MAX_CORRECTION_PERCENT: int = 75
MAX_CORRECTION_BAR_SIZE_FACTOR: int = 2
NUM_CONTEXT_CANDLES: int = 6

# Risk management
VOLUME_THRESHOLD_USDT: int = 5_000_000
MIN_RISK_REWARD: int = 3
PROGRESS_RISK_REWARD_NEAR: int = 1
PROGRESS_RISK_REWARD_FAR: int = 3
MIN_STOP_LOSS_PERCENT: float = 0.2
MIN_TAKE_PROFIT_PERCENT: float = 1.0

PUMP_MIN_DURATION_MINUTES: int = 20
PUMP_MAX_DURATION_MINUTES: int = 60 * 6
MIN_PRICE_GROWTH_PERCENT: int = 3
MIN_ATR_GROWTH_PERCENT: int = 50
MAX_RANGE_PERCENT_FOR_PUMP: int = 25
MIN_VOLUME_RATIO: int = 5
MIN_VOLUME_GROWTH: int = 100_000
BIG_BODY_ATR_MULTIPLIER: int = 2
MAX_BIG_BODY_SHARE: float = 0.5
ATR_PERIOD: int = 14

IS_FUNCTION_DURATION_LOG_ENABLED: bool = False
IS_CAPTURING_ENABLED: bool = True and not IS_FUNCTION_DURATION_LOG_ENABLED

# Trading control
IS_TRADING_ENABLED: bool = False
TRADE_POSITION_USDT: int = 10
MIN_COOLDOWN_PER_SYMBOL_MINUTES: int = 360  # 6 hours
SCAN_MAX_WORKERS: int = 5

# EMA periods and colors
EMA_PERIODS: list[int] = [20, 50, 100, 200]
EMA_COLORS: dict[int, str] = {
    20: '#ffe0b2',
    50: '#ffcc80',
    100: '#ffb74d',
    200: '#ffa726'
}
ATR_COLOR: str = '#ffa726'
SWING_COLOR_HIGH: str = '#ffe0b2'
SWING_COLOR_LOW: str = '#ffa726'
SWING_MARKER_SIZE: int = 25

# Candlestick width multiplier
CANDLESTICK_WIDTH_MULTIPLIER: float = 0.5

# X-axis date locator params
X_AXIS_MIN_TICKS: int = 15
X_AXIS_MAX_TICKS: int = 30
X_AXIS_MINUTELY_INTERVALS: list[int] = [5, 10, 15, 30, 60]
X_AXIS_TIME_FORMAT: str = '%H:%M'

# region Plotting constants
COLOR_BACKGROUND: str = '#0f0f0f'
COLOR_BACKGROUND_NA: str = '#111111'
COLOR_UP: str = '#078772'
COLOR_DOWN: str = '#d42f2f'
COLOR_TRENDLINE: str = '#765c99'
COLOR_PUMP_START: str = '#ffa726'
COLOR_OI: str = '#ffa726'

TRENDLINE_STYLE: str = '-'

PUMP_START_LINE_STYLE: str = '-'
PUMP_START_TEXT_SIZE: int = 8

EMA_ALPHA: float = 0.2
LINE_WIDTH: int = 1

PLOT_LEGEND_FONT_SIZE: int = 8
# endregion
