from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .event_logger import EventLogger
from .types import DefocusHandler, FocusHandler


@dataclass
class FocusController:
    focus_symbol: FocusHandler
    defocus_symbol: DefocusHandler
    logger: EventLogger
    _current: Optional[str] = None

    def focus(self, symbol: str, timestamp: datetime) -> None:
        if self._current == symbol:
            return
        if self._current is not None:
            self.defocus(timestamp)
        self.focus_symbol(symbol)
        self.logger.log_info(f"Фокус на {symbol}.", timestamp)
        self._current = symbol

    def defocus(self, timestamp: datetime) -> None:
        if self._current is None:
            return
        current = self._current
        self.defocus_symbol()
        self.logger.log_info(f"Дефокус со {current}.", timestamp)
        self._current = None

    @property
    def current(self) -> Optional[str]:
        return self._current


__all__ = ["FocusController"]
