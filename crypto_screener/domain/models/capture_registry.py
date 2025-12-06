from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from crypto_screener.domain.models.active_capture import ActiveCapture
from crypto_screener.domain.models.timeframe import Timeframe


@dataclass(frozen=True)
class CaptureKey:
    symbol: str
    timeframe: Timeframe


@dataclass
class CaptureRegistry:
    _captures: dict[CaptureKey, ActiveCapture] = field(default_factory=dict)

    def add(self, key: CaptureKey, capture: ActiveCapture) -> None:
        self._captures[key] = capture

    def get(self, key: CaptureKey) -> Optional[ActiveCapture]:
        return self._captures.get(key)

    def remove(self, key: CaptureKey) -> Optional[ActiveCapture]:
        return self._captures.pop(key, None)

    def has(self, key: CaptureKey) -> bool:
        return key in self._captures

    def has_symbol(self, symbol: str) -> bool:
        return any(key.symbol == symbol for key in self._captures)

    def items(self) -> Iterable[tuple[CaptureKey, ActiveCapture]]:
        return self._captures.items()

    def __contains__(self, key: CaptureKey) -> bool:  # pragma: no cover - convenience
        return self.has(key)
