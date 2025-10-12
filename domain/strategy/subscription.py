from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Set, Tuple

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
        active: List[str] = list(self._active)
        active_set: Set[str] = set(active)
        for symbol in additions:
            try:
                self.subscribe(symbol)
            except Exception as exc:  # noqa: BLE001
                self.logger.log(f"Не удалось подписаться на {symbol}: {exc}", timestamp)
                continue
            self.logger.log(f"Подписка на {symbol}.", timestamp)
            active.append(symbol)
            active_set.add(symbol)
        for symbol in removals:
            try:
                self.unsubscribe(symbol)
            except Exception as exc:  # noqa: BLE001
                self.logger.log(f"Не удалось отписаться от {symbol}: {exc}", timestamp)
                continue
            self.logger.log(f"Отписка от {symbol}.", timestamp)
            if symbol in active_set:
                active_set.remove(symbol)
            if symbol in active:
                active.remove(symbol)
        ordered = [symbol for symbol in symbols if symbol in active_set]
        extras = [symbol for symbol in active if symbol not in symbols]
        self._active = tuple(ordered + extras)

    def _compute_additions(self, symbols: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(symbol for symbol in symbols if symbol not in self._active)

    def _compute_removals(self, symbols: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(symbol for symbol in self._active if symbol not in symbols)


__all__ = ["SubscriptionManager"]
