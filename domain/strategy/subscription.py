from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Tuple

from .event_logger import EventLogger
from .types import SubscriptionHandler


@dataclass
class SubscriptionManager:
    subscribe: SubscriptionHandler
    unsubscribe: SubscriptionHandler
    logger: EventLogger
    _active: Tuple[str, ...] = field(default_factory=tuple)

    def update(self, symbols: Tuple[str, ...], timestamp: datetime) -> None:
        additions = self._compute_additions(symbols)
        removals = self._compute_removals(symbols)
        for symbol in additions:
            self.subscribe(symbol)
            self.logger.log(f"{timestamp:%H:%M:%S} Подписка на {symbol}.", timestamp)
        for symbol in removals:
            self.unsubscribe(symbol)
            self.logger.log(f"{timestamp:%H:%M:%S} Отписка от {symbol}.", timestamp)
        self._active = tuple(symbols)

    def _compute_additions(self, symbols: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(symbol for symbol in symbols if symbol not in self._active)

    def _compute_removals(self, symbols: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(symbol for symbol in self._active if symbol not in symbols)


__all__ = ["SubscriptionManager"]
