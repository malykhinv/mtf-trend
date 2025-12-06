from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from crypto_screener.domain.models.active_capture import ActiveCapture
from crypto_screener.domain.models.capture_registry import CaptureKey, CaptureRegistry
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.utils.time import utc_now


@dataclass
class CaptureState:
    def __init__(self) -> None:
        self.symbol: Optional[str] = None
        self.captures: CaptureRegistry = CaptureRegistry()

    def add_capture(
            self,
            symbol: str,
            timeframe: Timeframe,
            message_id: Optional[str],
            timeout_multiplier: int
    ) -> None:
        added_at = utc_now()
        deadline = added_at + timedelta(minutes=timeout_multiplier * timeframe.minutes)
        capture_key = CaptureKey(symbol, timeframe)
        self.captures.add(capture_key, ActiveCapture(
            added_at=added_at,
            deadline=deadline,
            message_id=message_id,
            is_setup_active=True,
        ))

    def remove_capture(
            self,
            symbol: str,
            timeframe: Timeframe
    ) -> Optional[ActiveCapture]:
        capture_key = CaptureKey(symbol, timeframe)
        return self.captures.remove(capture_key)

    def has_symbol_capture(
            self,
            symbol: str
    ) -> bool:
        return self.captures.has_symbol(symbol)
