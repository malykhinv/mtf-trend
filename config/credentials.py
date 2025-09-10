from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ApiCredentials:
    api_key: str
    api_secret: str


@dataclass(frozen=True)
class TelegramCredentials:
    orders_bot_token: str
    events_bot_token: str
    chat_id: str


_api_key = os.getenv("BINANCE_API_KEY")
_api_secret = os.getenv("BINANCE_API_SECRET")
_missing = [
    name
    for name, value in (
        ("BINANCE_API_KEY", _api_key),
        ("BINANCE_API_SECRET", _api_secret),
    )
    if not value
]
if _missing:
    raise RuntimeError(
        "Missing environment variable(s): {}. "
        "Set BINANCE_API_KEY and BINANCE_API_SECRET to your Binance API credentials.".format(
            ", ".join(_missing)
        )
    )

BINANCE = ApiCredentials(api_key=_api_key, api_secret=_api_secret)

TELEGRAM = TelegramCredentials(
    orders_bot_token=os.getenv("TELEGRAM_ORDERS_BOT_TOKEN", ""),
    events_bot_token=os.getenv("TELEGRAM_EVENTS_BOT_TOKEN", ""),
    chat_id=os.getenv("TELEGRAM_BOT_CHAT_ID", ""),
)
