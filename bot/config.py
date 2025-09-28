"""Global configuration constants for the trading bot."""
from __future__ import annotations

import os
from datetime import timedelta, timezone
from typing import Final
from zoneinfo import ZoneInfo

from bot.domain.models.exchange import Exchange
from bot.domain.models.timeframe import Timeframe

TIMEZONE_NAME = 'Europe/Belgrade'
TIMEZONE = ZoneInfo(TIMEZONE_NAME)
UTC = timezone.utc

DEFAULT_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.M1,
    Timeframe.M3,
    Timeframe.M5,
    Timeframe.M15,
)

VOL_WINDOW = 20
ATR_WINDOW = 14

MIN_REL_VOL = 40.0
MAX_REL_VOL = 200.0
MIN_ATR_MULT = 5.0
MIN_PCT_MOVE = 5.0
MAX_PCT_MOVE = 10.0
MAX_UPPER_WICK_PCT = 100.0
MAX_LOWER_WICK_PCT = 50.0

MIN_ORDER_USDT = 10.0
ORDER_PCT_OF_DEPOSIT = 0.0005
DEPOSIT_REFRESH_MIN = 60

TAKER_FEE_ENTRY_PCT = 0.04
TAKER_FEE_EXIT_PCT = 0.04

BREAKEVEN_TRIGGER_PCT = 0.5
BREAKEVEN_OFFSET_PCT = 0.3
TRAIL_SWING_WINDOW = 5
TRAIL_SWING_CONFIRM = 2

AGGR_WINDOW_SEC = 15
AGGR_IMBALANCE_THRESHOLD = 0.62

SYMBOL_COOLDOWN_SEC = 3600

BACKTEST_MIN_COVERAGE: Final[timedelta] = timedelta(days=30)

LOG_TIME_FMT = '%H:%M:%S'

ANOMALY_MIN_GROWTH_PCT = 3.0
ANOMALY_MIN_ATR_MULT = 3.0
ANOMALY_MIN_RELATIVE_VOLUME = 3.0
ANOMALY_MIN_VOLUME_SPIKE = 3.0

BINANCE_API_URL = 'https://fapi.binance.com'
BINANCE_API_KEY: str | None = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET: str | None = os.environ.get("BINANCE_API_SECRET")
BINANCE_RECV_WINDOW: int = 5000

BYBIT_API_URL = 'https://api.bybit.com'
BYBIT_API_KEY: str | None = os.environ.get("BYBIT_API_KEY")
BYBIT_API_SECRET: str | None = os.environ.get("BYBIT_API_SECRET")
BYBIT_RECV_WINDOW: int = 5000
BYBIT_TIMEOUT: float = 10.0
