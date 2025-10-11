from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Set, Tuple

from .event_logger import EventLogger
from .types import SubscriptionHandler


@dataclass
class SubscriptionManager:
    subscribe: SubscriptionHandler
    unsubscribe: SubscriptionHandler
    logger: EventLogger
    max_new_per_cycle: int
    max_total: int
    _active: Tuple[str, ...] = field(default_factory=tuple)
    _pending: Tuple[str, ...] = field(default_factory=tuple)
    _desired: Tuple[str, ...] = field(default_factory=tuple)
    _desired_order: Dict[str, int] = field(default_factory=dict)

    def update(self, symbols: Tuple[str, ...], timestamp: datetime) -> None:
        desired = tuple(symbols)
        desired_set: Set[str] = set(desired)
        self._desired = desired
        self._desired_order = {symbol: index for index, symbol in enumerate(desired)}

        active: List[str] = list(self._active)
        pending: List[str] = [symbol for symbol in self._pending if symbol in desired_set]

        active.sort(key=lambda symbol: self._desired_order.get(symbol, len(desired)))

        if self.max_total > 0:
            active_capacity = min(self.max_total, len(desired))
        else:
            active_capacity = 0

        kept: List[str] = []
        pending_set = set(pending)
        for symbol in active:
            if symbol not in desired_set:
                if not self._perform_unsubscribe(symbol, timestamp):
                    kept.append(symbol)
                continue
            if len(kept) < active_capacity:
                kept.append(symbol)
                continue
            if self._perform_unsubscribe(
                symbol, timestamp, reason="лимита активных подписок"
            ):
                if symbol not in pending_set:
                    pending.append(symbol)
                    pending_set.add(symbol)
                continue
            kept.append(symbol)

        for symbol in desired:
            if symbol in kept:
                continue
            if symbol in pending_set:
                continue
            pending.append(symbol)
            pending_set.add(symbol)

        self._active = tuple(kept)
        self._pending = tuple(pending)

    def activate_pending(self, timestamp: datetime) -> None:
        if self.max_total <= 0:
            return
        remaining_capacity = self.max_total - len(self._active)
        if remaining_capacity <= 0:
            return
        allowed_new = self.max_new_per_cycle if self.max_new_per_cycle > 0 else 0
        if allowed_new <= 0:
            return
        allowed_new = min(allowed_new, remaining_capacity)
        if allowed_new <= 0:
            return

        active = list(self._active)
        pending = list(self._pending)
        activated = 0
        while pending and activated < allowed_new and len(active) < self.max_total:
            symbol = pending.pop(0)
            try:
                self.subscribe(symbol)
            except Exception as exc:  # noqa: BLE001
                self.logger.log(f"Не удалось подписаться на {symbol}: {exc}", timestamp)
                pending.insert(0, symbol)
                break
            self.logger.log(f"Подписка на {symbol}.", timestamp)
            self._insert_active(active, symbol)
            activated += 1

        self._active = tuple(active)
        self._pending = tuple(pending)

    def _insert_active(self, active: List[str], symbol: str) -> None:
        order = self._desired_order
        position = order.get(symbol)
        if position is None:
            active.append(symbol)
            return
        index = len(active)
        for offset, current in enumerate(active):
            current_position = order.get(current)
            if current_position is None:
                continue
            if current_position > position:
                index = offset
                break
        active.insert(index, symbol)

    def _perform_unsubscribe(
        self, symbol: str, timestamp: datetime, reason: str | None = None
    ) -> bool:
        try:
            self.unsubscribe(symbol)
        except Exception as exc:  # noqa: BLE001
            self.logger.log(f"Не удалось отписаться от {symbol}: {exc}", timestamp)
            return False
        if reason:
            message = f"Отписка от {symbol} из-за {reason}."
        else:
            message = f"Отписка от {symbol}."
        self.logger.log(message, timestamp)
        return True


__all__ = ["SubscriptionManager"]
