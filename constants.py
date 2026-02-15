"""Модуль проекта."""

from __future__ import annotations

from datetime import timedelta

from domain.enums.entry_trigger import EntryTrigger
from domain.enums.sl_mode import SLMode
from domain.enums.timeframe import Timeframe

SUPPORTED_TIMEFRAMES: tuple[Timeframe, ...] = tuple(Timeframe)
DEFAULT_FETCH_TIMEFRAMES: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.M15)

# Лимиты АПИ
DEFAULT_FETCH_BATCH_SIZE = 1000
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
MILLISECONDS_IN_SECOND = 1000

# Таймауты (секунды)
HTTP_TIMEOUT_SECONDS = 30
EXCHANGE_TIMEOUT_SECONDS = 20
COINGECKO_TIMEOUT_SECONDS = 20

# Значения по умолчанию для загрузчиков
DEFAULT_REQUEST_TIMEOUT_SECONDS = 60

# Константы домена загрузки
TIMEFRAME_TO_DELTA = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M30: timedelta(minutes=30),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
    Timeframe.W1: timedelta(weeks=1),
}

OHLCV_FRAME_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
OPEN_INTEREST_FRAME_COLUMNS = ("timestamp", "open_interest")
CCXT_MARKET_TYPE_SWAP = "swap"
FUTURES_SETTLEMENT_QUOTE_ASSET = "USDT"
SIMULATION_COIN_SUFFIX_SLASH_USDT = "/usdt"
SIMULATION_COIN_SUFFIX_USDT = "usdt"

# Константы КоинГекко
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_HEADER_ACCEPT_KEY = "accept"
COINGECKO_HEADER_ACCEPT_JSON = "application/json"
COINGECKO_HEADER_API_KEY = "x-cg-demo-api-key"
COINGECKO_VS_CURRENCY_KEY = "vs_currency"
COINGECKO_VS_CURRENCY_USD = "usd"
COINGECKO_ORDER_KEY = "order"
COINGECKO_ORDER_MARKET_CAP_DESC = "market_cap_desc"
COINGECKO_PARAM_IDS = "ids"
COINGECKO_PARAM_PER_PAGE = "per_page"
COINGECKO_PARAM_PAGE = "page"
COINGECKO_PARAM_SPARKLINE = "sparkline"
COINGECKO_SPARKLINE_FALSE = "false"
COINGECKO_DEFAULT_PAGE = 1

# Константы логгера
LOGGER_DATE_FORMAT = "%H:%M:%S"
LOGGER_MESSAGE_FORMAT = "%(asctime)s %(message)s"
LOGGER_COLOR_RESET = "\033[0m"
LOGGER_COLOR_INFO = "\033[32m"
LOGGER_COLOR_WARNING = "\033[33m"
LOGGER_COLOR_ERROR = "\033[31m"
LOGGER_FILE_MAX_BYTES = 5 * 1024 * 1024
LOGGER_FILE_BACKUP_COUNT = 5
LOGGER_FILE_ENCODING = "utf-8"

# Глоссарий логирования (единые шаблоны)
LOG_MSG_LOAD_ERROR = "Ошибка загрузки %s: %s"
LOG_MSG_RETRY_EXHAUSTED = "Повторы исчерпаны: эндпоинт=%s символ=%s попыток=%s"
LOG_MSG_SKIP_UP_TO_DATE = "%s пропуск: %s уже актуален"
LOG_MSG_TASK_COMPLETED = "%s завершено"

# Константы подготовщика данных
DATA_PREPARER_NUMERIC_COLUMNS = ("open", "high", "low", "close", "volume", "open_interest")
DATA_PREPARER_EMPTY_FLOAT_DTYPE = "float64"
DATA_PREPARER_EMPTY_BOOL_DTYPE = "bool"
DATA_PREPARER_TRADE_COLUMNS = ("entry_time", "exit_time", "pnl", "pnl_percent", "result_type")
SIMULATION_ZERO_VALUE = 0.0
SIMULATION_UNIT_INCREMENT = 1.0

# Константы сетки пробоя
BREAKOUT_LOOKBACK_VALUES = (8, 13, 21)
BREAKOUT_VOLUME_MULT_VALUES = (1.2, 1.5, 2.0)
BREAKOUT_RETEST_WINDOW_VALUES = (12, 24, 36, 48)
BREAKOUT_RETEST_ZONE_VALUES = (0.002,)
BREAKOUT_RETEST_ZONE_ATR_VALUES = (0.75, 1.0, 1.25)
BREAKOUT_MIN_RR_VALUES = (2.5, 3.0, 3.5)
BREAKOUT_SL_MODE_VALUES = (SLMode.RETEST_EXTREME, SLMode.LEVEL, SLMode.BREAKOUT_EXTREME)
BREAKOUT_ENTRY_TRIGGER_VALUES = (EntryTrigger.IMMEDIATE, EntryTrigger.PRICE_CONFIRMATION)
BREAKOUT_TP2_MULT_VALUES = (1.5, 2.0, 2.5)
BREAKOUT_MIN_BODY_RATIO_VALUES = (0.4,)
BREAKOUT_MIN_MOVE_ATR_VALUES = (0.5,)
# Обратносуместимый алиас для старых импортов.
BREAKOUT_MIN_MOVE_FROM_BREAKOUT_VALUES = BREAKOUT_MIN_MOVE_ATR_VALUES
BREAKOUT_MAX_RETEST_DEPTH_VALUES = (1.0,)
BREAKOUT_CONFIRMATION_BARS_VALUES = (2,)
BREAKOUT_TARGET_PARAMETER_COMBINATIONS = 5832

# Значения по умолчанию для торговли
DEFAULT_COMMISSION_RATE = 0.0004
DEFAULT_SLIPPAGE = 0.0005
DEFAULT_SPREAD = 0.0
TP1_CLOSE_RATIO = 0.5

# Константы домена стратегии
STRATEGY_REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
STRATEGY_MIN_LOOKBACK = 5
STRATEGY_MIN_LOOKBACK_BUFFER = 5
STRATEGY_MIN_VOLUME_MULT = 0.0
STRATEGY_MIN_RR = 0.0
STRATEGY_MIN_TP2_MULT = 1.0
STRATEGY_RISK_FLOOR = 0.002
STRATEGY_POSITION_SIZE = 1.0
STRATEGY_DEFAULT_OPEN_INTEREST = 0.0
# Эпсилон нижней границы цены для расчетов отношений в геометрии свечи пробоя.
STRATEGY_PRICE_EPSILON = 1e-12
# Эпсилон нижней границы АТР/НАТР, чтобы избегать нулевых порогов в фильтрах ретеста.
STRATEGY_NATR_EPSILON = 1e-12

# Константы домена отчетности
REPORT_TRADES_COUNT_FILTER = 30
REPORT_PROFIT_FACTOR_FILTER = 1.0
REPORT_PROFITABLE_PF_THRESHOLD = 1.0
QUALITY_OI_MISSING_COLUMN_ISSUE = "oi_missing_column"
QUALITY_OI_ALIGNMENT_MISSING_VALUES_ISSUE = "oi_alignment_missing_values"
QUALITY_OI_ALIGNMENT_LEADING_GAPS_ISSUE = "oi_alignment_leading_gaps"
QUALITY_OI_ALIGNMENT_STALE_SERIES_ISSUE = "oi_alignment_stale_series"
QUALITY_SEVERITY_ERROR = "ERROR"
QUALITY_SEVERITY_WARNING = "WARNING"
QUALITY_SEVERITY_CRITICAL = "CRITICAL"
QUALITY_SEVERITY_INFO = "INFO"

# Константы домена бектеста
BACKTEST_EMPTY_PF = 0.0
BACKTEST_EMPTY_PNL_PERCENT = 0.0
BACKTEST_EMPTY_WIN_RATE = 0.0
BACKTEST_EMPTY_MAX_DD = 0.0
BACKTEST_EMPTY_TRADES_COUNT = 0
BACKTEST_ZERO_COUNT = 0
BACKTEST_SORT_ASCENDING = False
BACKTEST_ROUND_METRICS = 4
BACKTEST_ROUND_MAX_DD = 6
BACKTEST_PF_FALLBACK_WHEN_NO_LOSSES = 99.0
BACKTEST_PROFITABLE_PF_THRESHOLD = 1.0

# Константы домена симуляции/векторбт
SIMULATION_VECTORBT_DIRECTION = "longonly"
SIMULATION_INIT_CASH = 100.0
SIMULATION_SIZE = 1.0
SIMULATION_SIZE_TYPE = "amount"
SIMULATION_FEES = 0.0
SIMULATION_SLIPPAGE = 0.0
SIMULATION_FREQ = "1min"
SIMULATION_PRICE_INIT = 100.0
SIMULATION_PNL_PERCENT_DIVISOR = 100.0
SIMULATION_DATETIME_UNIT_MS = "ms"
SIMULATION_PARQUET_FILE_NAME = "data.parquet"

# Пороги качества данных
SPREAD_TO_CLOSE_WARNING_THRESHOLD = 0.3
OI_STALE_RATIO_THRESHOLD = 0.98
# Минимальное число выровненных сравнений ОИ, при котором оценка доли застоя имеет смысл.
OI_STALE_MIN_OBSERVATIONS = 3

# Пороги симуляции
# Абсолютная погрешность для классификации выходов безубыток/ТП2 по близости цены закрытия.
SIMULATION_PRICE_COMPARISON_EPSILON = 1e-8

# Значения по умолчанию для рантайма
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_CACHE_DIR = "./cache"
DEFAULT_LOGS_DIR = "./logs"
DEFAULT_RESULTS_DIR = "./cache/results"
DEFAULT_BACKTEST_OUTPUT_FILE = "backtest_results.csv"
DEFAULT_REPORT_OUTPUT_FILE = "report.json"
DEFAULT_QUALITY_REPORT_OUTPUT_FILE = "quality_report.json"

# Значения по умолчанию для интерфейса командной строки
DEFAULT_TOP_N = 50
DEFAULT_FETCH_DAYS = 60
DEFAULT_UPDATE_DAYS = 7
DEFAULT_MIN_VOLUME_USD = 20_000_000.0
DEFAULT_COINGECKO_MIN_REQUEST_INTERVAL_SECONDS = 2.1
DEFAULT_COINGECKO_VOLUME_BATCH_SIZE = 40
DEFAULT_LIQUIDITY_SKIP_ERROR_RATIO_THRESHOLD = 0.5

# Значения по умолчанию для сервиса

# Коды ошибок
ERROR_CODE_INVALID_CONFIG = "E_CFG_001"
ERROR_CODE_FETCH_FAILED = "E_FETCH_001"
ERROR_CODE_SIMULATION_FAILED = "E_SIM_001"
ERROR_CODE_DATA_QUALITY = "E_DQ_001"
ERROR_CODE_UNKNOWN = "E_UNKNOWN"
