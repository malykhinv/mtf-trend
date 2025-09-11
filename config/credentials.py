from dataclasses import dataclass
import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

@dataclass(frozen=True)
class ApiCredentials:
    api_key: str
    api_secret: str


@dataclass(frozen=True)
class TelegramCredentials:
    orders_bot_token: str
    events_bot_token: str
    chat_id: str


BINANCE = ApiCredentials(
    api_key=os.getenv("BINANCE_API_KEY", ""),
    api_secret=os.getenv("BINANCE_API_SECRET", "")
)

TELEGRAM = TelegramCredentials(
    orders_bot_token=os.getenv("TELEGRAM_ORDERS_BOT_TOKEN", ""),
    events_bot_token=os.getenv("TELEGRAM_EVENTS_BOT_TOKEN", ""),
    chat_id=os.getenv("TELEGRAM_BOT_CHAT_ID", ""),
)