from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, TYPE_CHECKING

from crypto_screener.domain.models.active_capture import ActiveCapture

if TYPE_CHECKING:
    from crypto_screener.domain.models.capture_registry import CaptureKey


@dataclass
class CaptureStorage:
    _storage: dict["CaptureKey", ActiveCapture] = field(default_factory=dict)

    def add(self, key: "CaptureKey", capture: ActiveCapture) -> None:
        self._storage[key] = capture

    def get(self, key: "CaptureKey") -> Optional[ActiveCapture]:
        return self._storage.get(key)

    def remove(self, key: "CaptureKey") -> Optional[ActiveCapture]:
        return self._storage.pop(key, None)

    def has(self, key: "CaptureKey") -> bool:
        return key in self._storage

    def has_symbol(self, symbol: str) -> bool:
        return any(key.symbol == symbol for key in self._storage)

    def items(self) -> Iterable[tuple["CaptureKey", ActiveCapture]]:
        return self._storage.items()
