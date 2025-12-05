from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.setup import Buy
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass
class ActiveTrade:
    symbol: str
    timeframe: Timeframe
    setup: Buy
    detection_time: datetime
    context: Context
    capture_message_id: Optional[str] = None
    postmortem_bars: list[Bar] = field(default_factory=list)
