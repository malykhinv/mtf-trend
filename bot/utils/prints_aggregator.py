"""Aggregates aggressive trade prints to compute imbalances."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Deque

from bot import config


@dataclass(slots=True)
class TradePrint:
    timestamp: datetime
    is_buy: bool
    quantity: float


class PrintsAggregator:
    def __init__(self) -> None:
        self._window = timedelta(seconds=config.AGGR_WINDOW_SEC)
        self._prints: Deque[TradePrint] = deque()

    def add_print(self, trade_print: TradePrint) -> None:
        self._prints.append(trade_print)
        self._drop_expired(trade_print.timestamp)

    def imbalance(self, now: datetime | None = None) -> float:
        reference_time = now or datetime.now(tz=config.UTC)
        self._drop_expired(reference_time)
        total_qty = sum(p.quantity for p in self._prints)
        if total_qty == 0:
            return 0.0
        buy_qty = sum(p.quantity for p in self._prints if p.is_buy)
        return buy_qty / total_qty

    def clear(self) -> None:
        self._prints.clear()

    def _drop_expired(self, now: datetime) -> None:
        cutoff = now - self._window
        while self._prints and self._prints[0].timestamp < cutoff:
            self._prints.popleft()
