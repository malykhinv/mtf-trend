from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from crypto_screener.domain.models.active_capture import ActiveCapture
from crypto_screener.domain.models.capture_storage import CaptureStorage
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class CaptureKey:
    symbol: str
    timeframe: Timeframe
    strategy: str


@dataclass
class CaptureRegistry:
    storage: CaptureStorage = field(default_factory=CaptureStorage)

    def add(self, key: CaptureKey, capture: ActiveCapture) -> None:
        self.storage.add(key, capture)

    def get(self, key: CaptureKey) -> Optional[ActiveCapture]:
        return self.storage.get(key)

    def remove(self, key: CaptureKey) -> Optional[ActiveCapture]:
        return self.storage.remove(key)

    def has(self, key: CaptureKey) -> bool:
        return self.storage.has(key)

    def has_symbol(self, symbol: str) -> bool:
        return self.storage.has_symbol(symbol)

    def items(self) -> Iterable[tuple[CaptureKey, ActiveCapture]]:
        return self.storage.items()

    def __contains__(self, key: CaptureKey) -> bool:  # pragma: no cover - convenience
        return self.has(key)
