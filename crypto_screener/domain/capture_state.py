from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional

from crypto_screener.domain.models.active_capture import ActiveCapture
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.utils.time import utc_now

@dataclass
class CaptureState:
    def __init__(self) -> None:
        self.symbol: Optional[str] = None
        self.captures: dict[tuple[str, Timeframe], ActiveCapture] = {}

    def add_capture(
            self,
            symbol: str,
            timeframe: Timeframe,
            message_id: Optional[str],
            timeout_multiplier: int
    ) -> None:
        added_at = utc_now()
        deadline = added_at + timedelta(minutes=timeout_multiplier * timeframe.minutes)
        self.captures[(symbol, timeframe)] = ActiveCapture(
            added_at=added_at,
            deadline=deadline,
            message_id=message_id,
            is_setup_active=True,
        )

    def remove_capture(
            self,
            symbol: str,
            timeframe: Timeframe
    ) -> Optional[ActiveCapture]:
        return self.captures.pop((symbol, timeframe), None)

    def has_symbol_capture(
            self,
            symbol: str
    ) -> bool:
        return any(key_symbol == symbol for key_symbol, _ in self.captures.keys())
