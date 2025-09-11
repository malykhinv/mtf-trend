from typing import Final

BINANCE_FAPI_WS: Final[str] = "wss://fstream.binance.com/stream"
BINANCE_FAPI_REST: Final[str] = "https://fapi.binance.com"

HTTP_TIMEOUT_CONNECT_SEC: Final[float] = 5.0
HTTP_TIMEOUT_READ_SEC: Final[float] = 15.0
HTTP_TIMEOUT_WRITE_SEC: Final[float] = 5.0
HTTP_TIMEOUT_POOL_SEC: Final[float] = 5.0

UNIVERSE_MIN_24H_USDT: Final[float] = 1_000_000.0
UNIVERSE_MAX_SPREAD_BPS: Final[float] = 8.0
UNIVERSE_MIN_TOP10_BID_USDT: Final[float] = 5_000.0

MAX_SYMBOLS = 180

EWMA_HALF_LIFE_MIN: Final[int] = 45
Z_BASE_WINDOW_MIN: Final[int] = 60

REST_POLL_SEC_OI: Final[int] = 20
REST_POLL_SEC_TAKER: Final[int] = 20
REST_POLL_SEC_PREMIUM: Final[int] = 20

TAKER_RATIO_PERIOD: Final[str] = "5m"
TAKER_RATIO_LIMIT: Final[int] = 1

COOLDOWN_AFTER_TRADE_SEC: Final[int] = 900
GLOBAL_BTC_PAUSE_Z: Final[float] = 3.0
GLOBAL_BTC_PAUSE_SEC: Final[int] = 180

RISK_PER_TRADE_USDT: Final[float] = 10.0
TRADE_INVALIDATION_SEC: Final[int] = 180
TASK_MAX_RESTARTS: Final[int] = 1
TASK_RESTART_DELAY_SEC: Final[int] = 1
