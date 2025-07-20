# region Общие параметры торговли
from zoneinfo import ZoneInfo

from domain.models.mtf_profile import MTFProfile
from domain.models.timeframe import Timeframe

BELGRADE_TZ = ZoneInfo("Europe/Belgrade")

# Multi-timeframe profiles
MTF_PROFILE_1_1 = MTFProfile(macro=Timeframe.D1, context=Timeframe.M30, setup=Timeframe.M1, entry=Timeframe.M1)
MTF_PROFILE_3_1 = MTFProfile(macro=Timeframe.D1, context=Timeframe.M30, setup=Timeframe.M3, entry=Timeframe.M1)
MTF_PROFILE_3_3 = MTFProfile(macro=Timeframe.D1, context=Timeframe.H1, setup=Timeframe.M3, entry=Timeframe.M3)
MTF_PROFILE_5_3 = MTFProfile(macro=Timeframe.D1, context=Timeframe.H1, setup=Timeframe.M5, entry=Timeframe.M3)
MTF_PROFILE_5_1 = MTFProfile(macro=Timeframe.D1, context=Timeframe.H1, setup=Timeframe.M5, entry=Timeframe.M1)

FLOAT_UNDEFINED = 0.0

MIN_BARS_BETWEEN_SWINGS = 3
MAX_RANGE_SIZE_PCT = 100
MAX_CORRECTION_PCT = 50
MAX_CORRECTION_BAR_SIZE_FACTOR = 2
ACTUAL_CONTEXT_CANDLES = 6

# Risk management
VOLUME_THRESHOLD_USDT = 5_000_000
MIN_RR = 3
PROGRESS_RR_NEAR = 1
PROGRESS_RR_FAR = 3
MIN_SL_PCT = 0.2
MIN_TP_PCT = 1.0

PUMP_MIN_MINUTES = 20
MIN_PRICE_GROWTH_PCT = 3
MIN_ATR_GROWTH_PCT = 50
MAX_RANGE_PCT = 25
VOLUME_RATIO_MIN = 5
MIN_VOLUME_GROWTH = 1_000_000
BIG_BODY_ATR_MULTIPLIER = 2
MAX_BIG_BODY_SHARE = 0.5
ATR_PERIOD = 14

# Trading control
IS_TRADING_ENABLED = False
POSITION_USDT = 10
MIN_COOLDOWN_PER_SYMBOL_MINUTES = 360  # 6 hours

# EMA periods and colors
EMA_PERIODS = [20, 50, 100, 200]
EMA_COLORS = {
    20: '#ffe0b2',
    50: '#ffcc80',
    100: '#ffb74d',
    200: '#ffa726'
}
ATR_COLOR = '#ffa726'
SWING_COLOR_HIGH = '#ffe0b2'
SWING_COLOR_LOW = '#ffa726'
SWING_SIZE = 25

# Candlestick width multiplier
CANDLE_WIDTH_MULTIPLIER = 0.5

# X-axis date locator params
X_AXIS_MIN_TICKS = 15
X_AXIS_MAX_TICKS = 30
X_AXIS_MINUTELY_INTERVALS = [5, 10, 15, 30, 60]
X_AXIS_TIME_FORMAT = '%H:%M'

# region Plotting constants
COLOR_FACE = '#0f0f0f'
COLOR_UP = '#078772'
COLOR_DOWN = '#d42f2f'
COLOR_TRENDLINE = '#765c99'
COLOR_PUMP_START = '#ffa726'

TRENDLINE_WIDTH = 1
TRENDLINE_STYLE = '-'

PUMP_START_LINE_STYLE = '-'
PUMP_START_LINE_WIDTH = 1
PUMP_START_TEXT_SIZE = 8

EMA_ALPHA = 0.2
EMA_LINEWIDTH = 1

LEGEND_FONT_SIZE = 8
# endregion
