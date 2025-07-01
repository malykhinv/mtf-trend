# region Общие параметры торговли
from domain.models.mtf_profile import MTFProfile
from domain.models.timeframe import Timeframe

# Multi-timeframe profiles
MTF_PROFILE_GLOBAL = MTFProfile(Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5)
MTF_PROFILE_INTRADAY = MTFProfile(Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5, Timeframe.M1)

# Trend and risk parameters
TREND_SIZE_ATR_FACTOR = 0.2
MAX_SWING_LOOKBACK_BARS = 5
MAX_RANGE_SIZE_PCT = 50
FLOAT_UNDEFINED = 0.0

# Risk management
VOLUME_THRESHOLD_USDT = 50_000_000
MIN_RR = 2.5
PROGRESS_RR_NEAR = 1
PROGRESS_RR_FAR = 3
MIN_SL_PCT = 0.2
MIN_TP_PCT = 1.0

# Trading control
IS_TRADING_ENABLED = False
POSITION_USDT = 10
MAX_TRADES_PER_HOUR = 3
MIN_COOLDOWN_PER_SYMBOL_MINUTES = 360  # 6 hours

# endregion

# region Swing and range settings
SWING_PROXIMITY_ATR_MULTIPLIER = 1.2
FLAT_MAX_CENTER_SHIFT_ATR = 1.0
TOUCH_DISTANCE_ATR = 1.5
# endregion

# region Candle and volume settings
STRONG_REACTION_VOLUME_MULTIPLIER = 1.2
MODERATE_REACTION_WICK_RATIO = 0.6
STRONG_REACTION_WICK_RATIO = 0.3
CANDLE_AVG_VOLUME_PERIOD = 20
# endregion

# region TP and SL settings
TP_LOOKAHEAD_BARS = 20
SL_LOOKBACK_BARS = 15
# endregion

# region Retest settings
RETEST_TOLERANCE_ATR = 0.75
# endregion
