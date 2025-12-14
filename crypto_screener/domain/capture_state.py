from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Optional

from crypto_screener.domain.models.active_capture import ActiveCapture
from crypto_screener.domain.models.capture_registry import CaptureKey, CaptureRegistry
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.utils.time import utc_now


@dataclass
class CaptureState:
    def __init__(self) -> None:
        self.captures: CaptureRegistry = CaptureRegistry()

    def _filtered_items(
            self,
            symbol: Optional[str] = None,
            timeframe: Optional[Timeframe] = None,
    ) -> Iterable[tuple[CaptureKey, ActiveCapture]]:
        return (
            (capture_key, capture)
            for capture_key, capture in self.captures.items()
            if (symbol is None or capture_key.symbol == symbol)
            and (timeframe is None or capture_key.timeframe == timeframe)
        )

    def add_capture(
            self,
            symbol: str,
            timeframe: Timeframe,
            strategy: str,
            message_id: Optional[str],
            timeout_multiplier: int
    ) -> None:
        added_at = utc_now()
        deadline = added_at + timedelta(minutes=timeout_multiplier * timeframe.minutes)
        capture_key = CaptureKey(symbol, timeframe, strategy)
        self.captures.add(capture_key, ActiveCapture(
            added_at=added_at,
            deadline=deadline,
            message_id=message_id,
            is_setup_active=True,
        ))

    def remove_capture(
            self,
            capture_key: CaptureKey,
    ) -> Optional[ActiveCapture]:
        return self.captures.remove(capture_key)

    def has_capture(self, capture_key: CaptureKey) -> bool:
        return self.captures.has(capture_key)

    def captures_for_symbol(self, symbol: str) -> list[tuple[CaptureKey, ActiveCapture]]:
        return list(self._filtered_items(symbol=symbol))

    def captures_for_timeframe(self, timeframe: Timeframe) -> list[tuple[CaptureKey, ActiveCapture]]:
        return list(self._filtered_items(timeframe=timeframe))

    def get_capture(self, capture_key: CaptureKey) -> Optional[ActiveCapture]:
        return self.captures.get(capture_key)

    def has_symbol_capture(self, symbol: str) -> bool:
        return any(self._filtered_items(symbol=symbol))

    def has_timeframe_capture(self, symbol: str, timeframe: Timeframe) -> bool:
        return any(
            capture_key.symbol == symbol and capture_key.timeframe == timeframe
            for capture_key, _ in self.captures.items()
        )

    def is_empty(self) -> bool:
        return not any(self.captures.items())
