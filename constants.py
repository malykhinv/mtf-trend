from typing import Final

BINANCE_FAPI_WS: Final[str] = "wss://fstream.binance.com/stream"
BINANCE_FAPI_REST: Final[str] = "https://fapi.binance.com"

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

RISK_PER_TRADE_USDT: Final[float] = 100.0  # настрой под депозит
