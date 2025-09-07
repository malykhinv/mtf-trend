from pathlib import Path
from typing import Final

BINANCE_FAPI_WS: Final[str] = "wss://fstream.binance.com/stream"
BINANCE_FAPI_REST: Final[str] = "https://fapi.binance.com"

UNIVERSE_MIN_24H_USDT: Final[float] = 20_000_000.0
UNIVERSE_MAX_SPREAD_BPS: Final[float] = 4.0
UNIVERSE_MIN_TOP10_BID_USDT: Final[float] = 80_000.0

EWMA_HALF_LIFE_MIN: Final[int] = 45
Z_BASE_WINDOW_MIN: Final[int] = 60

REST_POLL_SEC_OI: Final[int] = 20
REST_POLL_SEC_TAKER: Final[int] = 20
REST_POLL_SEC_PREMIUM: Final[int] = 20

COOLDOWN_AFTER_TRADE_SEC: Final[int] = 900
GLOBAL_BTC_PAUSE_Z: Final[float] = 3.0
GLOBAL_BTC_PAUSE_SEC: Final[int] = 180

RISK_PER_TRADE_USDT: Final[float] = 100.0  # настрой под депозит

# paths and limits
BASE_DIR: Final[Path] = Path(__file__).resolve().parent
DATA_DIR: Final[Path] = BASE_DIR / "data"
LOG_DIR: Final[Path] = DATA_DIR / "logs"
CACHE_DIR: Final[Path] = DATA_DIR / "cache"
RUNTIME_DIR: Final[Path] = DATA_DIR / "runtime"
BASELINE_DB_PATH: Final[Path] = CACHE_DIR / "baseline.sqlite"

LOG_MAX_BYTES: Final[int] = 50 * 1024 * 1024  # 50 MB rotation
LOG_BACKUP_COUNT: Final[int] = 5
