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


BINANCE = ApiCredentials(api_key="REPLACE_ME", api_secret="REPLACE_ME")

TELEGRAM = TelegramCredentials(
    orders_bot_token=os.getenv("TELEGRAM_ORDERS_BOT_TOKEN", ""),
    events_bot_token=os.getenv("TELEGRAM_EVENTS_BOT_TOKEN", ""),
    chat_id=os.getenv("TELEGRAM_BOT_CHAT_ID", ""),
)
