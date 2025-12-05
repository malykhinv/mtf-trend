from __future__ import annotations

from dataclasses import dataclass

from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass
class AllowedTrade:
    symbol: str
    timeframe: Timeframe
    message_id: str
    context: Context
