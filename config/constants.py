# region Общие параметры торговли
from domain.models.mtf_profile import MTFProfile
from domain.models.timeframe import Timeframe

# Multi-timeframe profiles
MTF_PROFILE_1_1 = MTFProfile(macro=Timeframe.D1, setup=Timeframe.M1, entry=Timeframe.M1)
MTF_PROFILE_3_1 = MTFProfile(macro=Timeframe.D1, setup=Timeframe.M3, entry=Timeframe.M1)
MTF_PROFILE_3_3 = MTFProfile(macro=Timeframe.D1, setup=Timeframe.M3, entry=Timeframe.M3)
MTF_PROFILE_5_3 = MTFProfile(macro=Timeframe.D1, setup=Timeframe.M5, entry=Timeframe.M3)
MTF_PROFILE_5_1 = MTFProfile(macro=Timeframe.D1, setup=Timeframe.M5, entry=Timeframe.M1)

FLOAT_UNDEFINED = 0.0

MIN_BARS_BETWEEN_SWINGS = 3
MAX_RANGE_SIZE_PCT = 100
MAX_CORRECTION_PCT = 50

# Risk management
VOLUME_THRESHOLD_USDT = 5_000_000
MIN_RR = 3
PROGRESS_RR_NEAR = 1
PROGRESS_RR_FAR = 3
MIN_SL_PCT = 0.2
MIN_TP_PCT = 1.0

CONSOLIDATION_HOURS = 24
PUMP_MIN_MINUTES = 20
MIN_PUMP_PCT = 5
MAX_RANGE_PCT = 5
VOLUME_RATIO_MIN = 5
BIG_BODY_ATR_MULTIPLIER = 2
MAX_BIG_BODY_SHARE = 0.5

# Trading control
IS_TRADING_ENABLED = False
POSITION_USDT = 10
MIN_COOLDOWN_PER_SYMBOL_MINUTES = 360  # 6 hours