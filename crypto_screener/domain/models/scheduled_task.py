from dataclasses import dataclass, field
from datetime import datetime

from crypto_screener.domain.models.symbol import FuturesSymbol
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(order=True)
class ScheduledTask:
    next_run_at: datetime
    priority: int = field(default=0)
    symbol: FuturesSymbol = field(compare=False)
    timeframe: Timeframe = field(compare=False)
