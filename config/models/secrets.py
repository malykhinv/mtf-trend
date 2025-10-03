from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Secrets:
    tg_bot_token: str
    binance_api_key: Optional[str]
    binance_api_secret: Optional[str]
    bybit_api_key: Optional[str]
    bybit_api_secret: Optional[str]


__all__ = ["Secrets"]
